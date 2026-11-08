"""Experimental FFmpeg canvas recorder. Frame rendering must run on the Qt GUI thread.

The encoder runs on a dedicated thread. A bounded frame queue avoids blocking Qt on
FFmpeg input. Sounddevice supplies bounded microphone chunks processed on a worker thread and
written to temporary WAV. The final MP4 is muxed once Stop is pressed; never screen-captures any window.
"""
import queue
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
import sys
from types import SimpleNamespace
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QImage
from native_engine import audio_backend_status
from audio_effects import process_chain, normalize_chain, DEFAULT_CHAIN
from audio_diagnostics import snapshot as audio_diagnostics_snapshot


_ffmpeg_paths={}

def resolve_ffmpeg(configured=''):
    """Resolve once per configured location, never scan PATH during every frame."""
    key=str(configured or '')
    if key in _ffmpeg_paths:return _ffmpeg_paths[key]
    result=_locate_ffmpeg(key)
    if result:
        _ffmpeg_paths[key]=result
        while len(_ffmpeg_paths)>16:_ffmpeg_paths.pop(next(iter(_ffmpeg_paths)))
    return result


def _locate_ffmpeg(configured=''):
    app_dir=Path(__file__).resolve().parent
    candidates=[configured,Path(sys.executable).parent/'ffmpeg.exe',app_dir/'ffmpeg.exe',app_dir/'ffmpeg',
                app_dir/'bin'/'ffmpeg.exe',app_dir/'bin'/'ffmpeg']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():return str(Path(candidate).resolve())
    system=shutil.which('ffmpeg')
    if system:return system
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError,RuntimeError):return None


@lru_cache(maxsize=8)
def supports_alpha_conversion(ffmpeg):
    try:
        result=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-alpha_mode','premultiplied',
            '-f','lavfi','-i','color=s=16x16','-frames:v','0','-f','null','-'],
            capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return result.returncode==0
    except (OSError,subprocess.TimeoutExpired):return False


class CanvasRecorder:
    def __init__(self, filename, width, height, fps, microphone=True, ffmpeg_path="", audio_gain=100, audio_gate_db=-60, audio_gate_enabled=False, audio_chain=None, audio_clip_drive=150, editable=False, alpha=False, lossless_rgb=False):
        self.editable = editable
        self.packed=bool(editable)
        self.alpha = alpha
        self.alpha_conversion_offloaded=False
        self.lossless_rgb=lossless_rgb
        self.companions = []
        self.editable_failed=False
        self.render_project = {}
        self.path = Path(filename)
        self.width, self.height, self.fps = int(width), int(height), int(fps)
        self.microphone = microphone
        self.audio_gain = int(audio_gain)
        self.audio_gate_db = int(audio_gate_db)
        self.audio_gate_enabled = bool(audio_gate_enabled)
        self.audio_chain = normalize_chain(audio_chain if audio_chain is not None else [dict(type="gain", enabled=True), dict(type="gate", enabled=audio_gate_enabled), dict(type="clip", enabled=False)])
        self.audio_clip_drive = int(audio_clip_drive)
        self.audio_dsp_backend = audio_backend_status()
        self.audio_peak = 0
        self.ffmpeg_path = ffmpeg_path
        self.frame_count = 0
        self.late_frames = 0
        self.error = None
        self.stopping = False
        self.finished = False
        self.cancelled = False
        self._frames = queue.Queue(maxsize=12)
        self._audio_queue = queue.Queue(maxsize=256)
        self.audio_listener = None
        self.audio_level_listener = None
        self._audio_worker = None
        self.audio_dropped_chunks = 0
        self._temp = None
        self._video = None
        self._audio = None
        self._stream = None
        self.worker_stalls=[]
        self._encoder_pending_since=0.
        self._audio_pending_since=0.
        self.audio_stage='idle'
        self._last_bytes = None
        self.capture_calls = 0
        self.capture_stage_totals={}
        self.capture_total_ms = 0.0
        self.capture_max_ms = 0.0
        self.encoder_writes = 0
        self.encoded_unique_frames=0
        self.queue_batches_coalesced=0
        self.encoder_write_total_ms = 0.0
        self.encoder_write_max_ms = 0.0
        self.peak_queue_depth = 0
        self.duplicate_frames = 0
        self.audio_samples = 0
        self.plugin_latency_samples = 0
        self._latency_remaining = 0
        self.processing_options = {}
        self.processing_result = {}
        self.processing_error = ''
        self.processing_stage = ''
        self.processing_progress={}
        self.processing_session=None
        self.awaiting_review=False
        self.script = ''
        self.model_path = ''
        self._finish_started = False
        self.report_path = self.path.with_suffix('.diagnostics.json')

    def start(self):
        ffmpeg = resolve_ffmpeg(self.ffmpeg_path)
        self._ffmpeg = ffmpeg
        if not ffmpeg:
            raise RuntimeError('FFmpeg not found. Open Settings â†’ FFmpeg Location to select ffmpeg.exe, or put it beside app.py.')
        if self.microphone:
            from vst2_host import latency, hide_editors
            hide_editors()
            self.plugin_latency_samples = latency(self.audio_chain, refresh=True)
            self._latency_remaining = self.plugin_latency_samples
        self._temp = tempfile.TemporaryDirectory(prefix='dt_record_')
        self._silent = Path(self._temp.name) / 'video.mp4'
        self._wave = Path(self._temp.name) / 'microphone.wav'
        command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                   '-f', 'rawvideo', '-pixel_format', 'bgra', '-video_size',
                   f'{self.width}x{self.height}', '-framerate', str(self.fps),
                   '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-preset', 'ultrafast',
                   '-threads','1','-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(self._silent)]
        if self.lossless_rgb:
            command=command[:command.index('-c:v')]+['-c:v','libx264rgb','-preset','ultrafast','-crf','0','-threads','1','-pix_fmt','bgr0',str(self._silent)]
        if self.alpha:
            self._silent = Path(self._temp.name) / 'foreground.mov'
            command = command[:command.index('-c:v')] + ['-c:v','qtrle','-threads','1','-pix_fmt','argb',str(self._silent)]
        if self.alpha and supports_alpha_conversion(ffmpeg):
            self.alpha_conversion_offloaded=True
            index=command.index('-i')
            command[index:index]=['-alpha_mode','premultiplied']
            command[-1:-1]=['-alpha_mode','premultiplied']
        if self.packed:
            self._silent=Path(self._temp.name)/'packed.mkv'
            self._packed_source=self._silent
            command=[ffmpeg,'-y','-hide_banner','-loglevel','error','-filter_complex_threads','1','-f','rawvideo','-pixel_format','bgra',
                     '-probesize','32','-analyzeduration','0','-video_size',f'{self.width}x{self.height*3}','-framerate',str(self.fps),'-i','pipe:0',
                     '-filter_complex',f'[0:v]split=2[color][mask];[mask]crop={self.width}:{self.height}:0:{self.height*2},alphaextract[alpha]',
                     '-map','[color]','-c:v:0','libx264','-preset:v:0','ultrafast','-crf:v:0','23','-pix_fmt:v:0','yuv420p','-threads:v:0','2','-tune:v:0','zerolatency',
                     '-map','[alpha]','-c:v:1','libx264','-preset:v:1','ultrafast','-tune:v:1','zerolatency','-qp:v:1','0','-pix_fmt:v:1','gray','-color_range:v:1','pc','-threads:v:1','1',str(self._silent)]
            for name,alpha in [('background.mp4',False),('foreground.mov',True)]:
                track=SimpleNamespace(path=Path(self._temp.name)/name,alpha=alpha,frame_count=0,error=None,finished=False,
                    alpha_conversion_offloaded=True,encoder_write_total_ms=0.,encoder_writes=0,peak_queue_depth=0)
                track.finish_async=lambda t=track:setattr(t,'finished',True)
                track.abort=lambda t=track:setattr(t,'finished',True)
                self.companions.append(track)
        self._video = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            if self.microphone:
                import sounddevice as sd
                self._sd = sd
                self._audio_rate = 48000
                self._audio_file = wave.open(str(self._wave), 'wb')
                self._audio_file.setnchannels(1)
                self._audio_file.setsampwidth(2)
                self._audio_file.setframerate(self._audio_rate)
                def callback(indata, frames, time_info, status):
                    if self.cancelled or self.stopping:
                        return
                    self.audio_samples += frames
                    try:
                        self._audio_queue.put_nowait(bytes(indata))
                    except queue.Full:
                        self.audio_dropped_chunks += 1
                self._stream = sd.RawInputStream(samplerate=self._audio_rate, channels=1,
                                                 dtype='int16', callback=callback)
                self._stream.start()
                self._audio_worker = threading.Thread(target=self._process_audio, daemon=True)
                self._audio_worker.start()
        except Exception:
            self.abort()
            raise
        self._started = time.monotonic()
        self._writer = threading.Thread(target=self._write_frames, daemon=True)
        self._writer.start()
        self._watchdog=threading.Thread(target=self._watch_workers,name="Recording watchdog",daemon=True)
        self._watchdog.start()

    def _watch_workers(self):
        import traceback
        last_report=0.
        while not self.finished and not self.cancelled:
            time.sleep(.5)
            now=time.monotonic()
            if now-last_report<5:continue
            waits={name:round(now-start,2) for name,start in [('encoder',self._encoder_pending_since),('audio',self._audio_pending_since)] if start and now-start>2}
            if not waits:continue
            frames=sys._current_frames()
            snapshot=dict(waits=waits,audio_stage=self.audio_stage,threads={thread.name:''.join(traceback.format_stack(frames[thread.ident])) for thread in threading.enumerate() if thread.ident in frames})
            self.worker_stalls=(self.worker_stalls+[snapshot])[-3:]
            try:
                from update_core import atomic_json
                atomic_json(self.path.with_suffix('.stall.json'),dict(worker_stalls=self.worker_stalls))
            except OSError:pass
            last_report=now

    def elapsed(self):
        return getattr(self, '_stopped', time.monotonic()) - self._started

    def frames_due(self):
        return max(0, int(self.elapsed() * self.fps) + 1 - self.frame_count)

    def note_capture(self, elapsed_ms, stages=None):
        self.capture_calls += 1
        self.capture_total_ms += elapsed_ms
        for key,value in (stages or {}).items():self.capture_stage_totals[key]=self.capture_stage_totals.get(key,0.)+value
        self.capture_max_ms = max(self.capture_max_ms, elapsed_ms)

    def push_frame(self, image, background=None, foreground=None):
        if self.stopping or self.error:
            return
        # QImage is reentrant and implicitly shared. Queue an owned image snapshot;
        # conversion and byte copies belong to the encoder worker, never the UI.
        if self.packed:
            if background is None or foreground is None:
                self.error='Editable source frame missing; capture stopped.'
                return
            data=tuple(QImage(part) for part in (image,background,foreground))
        else:data=QImage(image)
        due = min(self.frames_due(), self.fps * 2)
        if due > 1:
            self.late_frames += due - 1
        if due > 1 and self._last_bytes:
            self.duplicate_frames += due - 1
        if due:
            # A timing catch-up is one bounded batch, not dozens of identical
            # queue entries. Pixel ownership remains bounded by distinct frames.
            packet=SimpleNamespace(pixels=data,previous=self._last_bytes,count=due)
            try:self._frames.put_nowait(packet)
            except queue.Full:
                if self.frame_count-self.encoder_writes+due>self.fps*2:
                    self.error='Video encoder remained over two seconds behind; recording stopped with recovery.'
                    return
                # Coalesce the oldest timing span, retaining its first image and
                # the next span's latest image. Memory stays bounded; time is not cut.
                with self._frames.mutex:
                    if len(self._frames.queue)>=self._frames.maxsize:
                        old=self._frames.queue.popleft()
                        following=self._frames.queue[0]
                        following.count+=old.count
                        following.previous=old.previous if old.count>1 and old.previous is not None else old.pixels
                        self.queue_batches_coalesced+=1
                        self._frames.unfinished_tasks-=1
                    self._frames.queue.append(packet)
                    self._frames.unfinished_tasks+=1
                    self._frames.not_empty.notify()
            self.frame_count+=due
            self.peak_queue_depth=max(self.peak_queue_depth,self._frames.qsize())
        self._last_bytes = data
        if self.packed:
            for track in self.companions:track.frame_count=self.frame_count

    def _write_frames(self):
        try:
            last_written_key=None
            while not self.cancelled:
                packet=self._frames.get()
                if packet is None:break
                for index in range(packet.count):
                    if self.cancelled:break
                    pixels=packet.previous if index<packet.count-1 and packet.previous is not None else packet.pixels
                    self._encoder_pending_since=time.monotonic()
                    started=time.perf_counter()
                    if isinstance(pixels,tuple):
                        for plane in pixels:
                            native=plane.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                            self._video.stdin.write(native.constBits())
                    else:
                        pixel_format=QImage.Format.Format_ARGB32 if self.alpha and not self.alpha_conversion_offloaded else QImage.Format.Format_ARGB32_Premultiplied
                        native=pixels.convertToFormat(pixel_format)
                        self._video.stdin.write(native.constBits())
                    elapsed=(time.perf_counter()-started)*1000
                    self._encoder_pending_since=0.
                    key=tuple(plane.cacheKey() for plane in pixels) if isinstance(pixels,tuple) else pixels.cacheKey()
                    if key!=last_written_key:self.encoded_unique_frames+=1
                    last_written_key=key
                    self.encoder_writes+=1
                    self.encoder_write_total_ms+=elapsed
                    self.encoder_write_max_ms=max(self.encoder_write_max_ms,elapsed)
        except Exception as exc:
            if not self.cancelled:self.error='Video writing failed: '+str(exc)

    def _process_audio(self):
        """Continuous audio DSP off the PortAudio callback; playback receives same PCM."""
        while not self.cancelled:
            try:
                data = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                if self.stopping:
                    break
                continue
            gain = int(self.audio_gain)
            gate = bool(self.audio_gate_enabled)
            db = int(self.audio_gate_db)
            try:
                self._audio_pending_since=time.monotonic()
                self.audio_stage='microphone level'
                if self.audio_level_listener is not None:
                    self.audio_level_listener(data)
                self.audio_stage='effects'
                data, peak = process_chain(data, self.audio_chain, gain, db, self.audio_clip_drive)
                self.audio_peak = max(self.audio_peak, peak)
                self.audio_stage='writing audio'
                self._write_aligned_audio(data)
                listener = self.audio_listener
                if listener is not None:
                    self.audio_stage='monitor'
                    listener(data)
                self._audio_pending_since=0.
                self.audio_stage='idle'
            except Exception as exc:
                self.error = 'Audio processing failed: ' + str(exc)
                break
        # Flush algorithmic lookahead only for Stop/Save. Discard never does DSP
        # finalization. Trimming the initial delay and draining exactly that many
        # samples preserves the captured duration and video alignment.
        if not self.cancelled and self.stopping and not self.error:
            try:
                remaining = self.plugin_latency_samples
                while remaining > 0 and not self.cancelled:
                    count = min(2048, remaining)
                    data, _ = process_chain(bytes(count * 2), self.audio_chain,
                        int(self.audio_gain), int(self.audio_gate_db), self.audio_clip_drive)
                    self._write_aligned_audio(data)
                    remaining -= count
            except Exception as exc:
                self.error = 'Audio finalization failed: ' + str(exc)

    def _write_aligned_audio(self, data):
        skip = min(self._latency_remaining, len(data) // 2)
        self._latency_remaining -= skip
        if len(data) > skip * 2:
            self._audio_file.writeframesraw(data[skip * 2:])

    def pause_capture(self):
        if self.stopping:return
        self._stopped=time.monotonic()
        # Reject callbacks immediately; potentially slow device teardown runs in _finish.
        self.stopping=True

    def finish_async(self):
        if self._finish_started or self.cancelled:
            return
        self.pause_capture()
        self._finish_started = True
        threading.Thread(target=self._finish, daemon=True).start()

    def diagnostic_report(self):
        elapsed = self.elapsed() if hasattr(self, '_started') else 0.0
        audio_seconds = self.audio_samples / self._audio_rate if self.microphone and hasattr(self, '_audio_rate') else None
        return {
            'app_release': __import__('version').VERSION, 'output': self.path.name,
            'resolution': [self.width, self.height], 'target_fps': self.fps,
            'wall_clock_seconds': round(elapsed, 3),
            'video_duration_seconds': round(self.frame_count / self.fps, 3),
            'audio_dsp_backend': self.audio_dsp_backend,
            'plugin_latency_compensated_samples': self.plugin_latency_samples,
            'audio_effect_chain': self.audio_chain,
            'audio_soft_clip_drive_percent': self.audio_clip_drive,
            'audio_gain_percent': self.audio_gain,
            'audio_gate_enabled': self.audio_gate_enabled,
            'audio_gate_threshold_dbfs': self.audio_gate_db if self.audio_gate_enabled else None,
            'audio_processed_peak': self.audio_peak,
            'audio_dropped_chunks': self.audio_dropped_chunks,
            'audio_dsp_timings': audio_diagnostics_snapshot(),
            'worker_stalls':self.worker_stalls,
            'capture_bytes':getattr(self,'capture_bytes',None),
            'encoded_frames': self.frame_count, 'late_frames': self.late_frames,
            'duplicated_frames': max(self.duplicate_frames,self.frame_count-self.encoded_unique_frames),
            'capture_calls': self.capture_calls,
            'encoded_unique_frames':self.encoded_unique_frames,
            'fresh_encoded_fps':round(self.encoded_unique_frames/elapsed,2) if elapsed else 0,
            'queue_batches_coalesced':self.queue_batches_coalesced,
            'fresh_capture_fps':round(self.capture_calls/elapsed,2) if elapsed else 0,
            'duplicate_frame_percent':round(100*max(self.duplicate_frames,self.frame_count-self.encoded_unique_frames)/max(1,self.frame_count),2),
            'canvas_stage_mean_ms':{key:round(value/max(1,self.capture_calls),2) for key,value in self.capture_stage_totals.items()},
            'editable_capture_mode':'compressed_video_lossless_alpha' if self.packed else 'standard',
            'editable_tracks':[dict(alpha=track.alpha,frames=track.frame_count,error=track.error) for track in self.companions],
            'capture_mean_ms': round(self.capture_total_ms / max(1, self.capture_calls), 2),
            'capture_max_ms': round(self.capture_max_ms, 2),
            'encoder_writes': self.encoder_writes,
            'encoder_write_mean_ms': round(self.encoder_write_total_ms / max(1, self.encoder_writes), 2),
            'encoder_write_max_ms': round(self.encoder_write_max_ms, 2),
            'encoder_queue_peak': self.peak_queue_depth,
            'encoder_queue_capacity': self._frames.maxsize,
            'microphone_duration_seconds': round(audio_seconds, 3) if audio_seconds is not None else None,
            'audio_video_duration_difference_seconds': round(audio_seconds - self.frame_count / self.fps, 3) if audio_seconds is not None else None,
            'canvas_stage_last_ms': getattr(self, 'render_stages', {}),
            'live2d_pose_timings': __import__('live2d_runtime').pose_diagnostics(),
            'recording_error': self.error,
            'processing': {key: value for key, value in self.processing_result.items() if key != 'alignment'},
            'processing_error': self.processing_error,
            'post_processing':self.processing_session.report if self.processing_session else {},
            'processing_directory':str(self.processing_session.directory) if self.processing_session else None,
            'notes': 'Timing diagnostics are indicative; not a frame-perfect AV synchronization measurement.'
        }

    def _finish(self):
        from processing_session import ProcessingSession,_current
        token=None
        try:
            def update(state):
                self.processing_progress=state;self.processing_stage=state['stage']
            self.processing_session=ProcessingSession(self.path.with_suffix('.processing'),update)
            self.processing_session.duration=self.frame_count/self.fps
            self.processing_session.report['diagnostics_path']=str(self.report_path)
            self.processing_session.report['process_choice_wait_seconds']=max(0,time.monotonic()-getattr(self,'_stopped',time.monotonic()))
            token=_current.set(self.processing_session)
            self.processing_session.begin('Finalizing capture')
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
            audio_failure=None
            if self._audio_worker is not None:
                self._audio_worker.join(timeout=10)
                if self._audio_worker.is_alive():
                    audio_failure='Audio DSP worker timed out'
                    self._retain_live_audio=True
            if hasattr(self, '_audio_file') and not audio_failure:
                self._audio_file.close()
            self._frames.put(None, timeout=15)
            self._writer.join(timeout=60)
            if self._writer.is_alive():
                raise RuntimeError('Encoder frame writer timed out')
            self._video.stdin.close()
            self._video.stdin=None
            _,errors=self._video.communicate(timeout=60)
            diagnostics=errors.decode('utf-8','replace')
            code=self._video.returncode
            if code != 0:
                raise RuntimeError('FFmpeg video encoding failed: ' + diagnostics[-1500:])
            self.capture_bytes=self._silent.stat().st_size
            if audio_failure:
                raise RuntimeError(audio_failure)
            if self.error:
                raise RuntimeError(self.error)
            if self.frame_count == 0:
                raise RuntimeError('No frames were recorded')
            if self.packed:
                self.processing_stage='Preparing editable source tracksâ€¦'
                captured=self._silent.with_name('captured.mp4')
                graph=self._silent.with_name('unpack.ffscript')
                tagged=supports_alpha_conversion(self._ffmpeg)
                alpha_tag=',setparams=alpha_mode=premultiplied' if tagged else ''
                graph.write_text(f'[0:v]split=3[c][b][f];[c]crop={self.width}:{self.height}:0:0[capture];'
                    f'[b]crop={self.width}:{self.height}:0:{self.height}[background];'
                    f'[f]crop={self.width}:{self.height}:0:{self.height*2}[foreground_color];[foreground_color][0:v:1]alphamerge{alpha_tag}[foreground]',encoding='utf-8')
                from edit_pipeline import run_media
                tagged=supports_alpha_conversion(self._ffmpeg)
                command=[self._ffmpeg,'-y','-v','error']+(['-alpha_mode','premultiplied'] if tagged else [])+[
                    '-i',str(self._silent),'-/filter_complex',str(graph),
                    '-map','[capture]','-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-threads','1',str(captured),
                    '-map','[background]','-c:v','libx264rgb','-preset','ultrafast','-crf','0','-pix_fmt','bgr0','-threads','1',str(self.companions[0].path),
                    '-map','[foreground]','-c:v','qtrle','-pix_fmt','argb','-threads','1']
                if tagged:command+=['-alpha_mode','premultiplied']
                command+=[str(self.companions[1].path)]
                run_media(command)
                self._silent=captured
            ready = self._silent.with_name('ready.mp4')
            if self.microphone and self._wave.exists() and self._wave.stat().st_size > 44:
                command = [self._ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                           '-i', str(self._silent), '-i', str(self._wave),
                           '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
                           '-b:a', '160k', '-shortest', '-movflags', '+faststart', str(ready)]
                self.processing_session.begin('Combining video and audio')
                self.processing_session.run(command,duration=self.frame_count/self.fps)
            else:
                shutil.copyfile(self._silent, ready)
            # Finish all editable tracks before analysis; each has exactly the main frame count.
            for companion in self.companions:
                if companion.error:
                    self.editable_failed=True
                    try:companion.abort()
                    except Exception:pass
                else:companion.finish_async()
            deadline=time.monotonic()+180
            for companion in self.companions:
                while not companion.finished and time.monotonic()<deadline:time.sleep(.02)
                if not companion.finished or companion.error or companion.frame_count!=self.frame_count:
                    self.editable_failed=True
                    self.processing_error='Editable source encoding failed; captured video retained. '+str(companion.error)
            if self.editable_failed:
                self.processing_options={**self.processing_options,'align':False,'advanced':'off'}
            selected = ready
            editable_ready=None
            if self.companions and not self.editable_failed and self.processing_options.get('align'):
                editable_ready=ready.with_name('editable.mp4')
                if self.microphone and self._wave.exists():
                    self.processing_session.run([self._ffmpeg,'-y','-v','error','-i',str(self.companions[0].path),'-i',str(self._wave),
                        '-map','0:v','-map','1:a','-c:v','copy','-c:a','aac','-shortest',str(editable_ready)],
                        check=True,capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                else:shutil.copyfile(self.companions[0].path,editable_ready)
                selected=editable_ready
            if self.processing_options:
                try:
                    from recording_processing import process_recording
                    selected, self.processing_result = process_recording(selected, self._ffmpeg,
                        {**self.processing_options,'source_fps':self.fps,'pronunciations':getattr(self,'pronunciations',[])}, self.script, self.model_path,
                        self.frame_count / self.fps, ready.parent,
                        lambda stage: setattr(self, 'processing_stage', stage))
                except Exception as exc:
                    self.processing_error = str(exc)
                    selected = ready  # Optional processing must never destroy a take.
            if 'alignment' in self.processing_result and editable_ready is not None:
                self.processing_session.begin('Preparing review sources')
                from edit_pipeline import cut_media, publish_file
                from update_core import atomic_json
                bundle=self.path.with_suffix('.take')
                with tempfile.TemporaryDirectory(prefix='.'+self.path.stem+'-',dir=self.path.parent) as folder:
                    stage=Path(folder)
                    shutil.copyfile(selected,stage/'scene.mp4')
                    shutil.copyfile(ready,stage/'capture.mp4')
                    segments=self.processing_result['trim_segments']
                    foreground=self.companions[1].path
                    if segments!=[(0.,self.frame_count/self.fps)]:
                        cut_media(foreground,stage/'foreground.mov',segments,self._ffmpeg,stage,audio=False,alpha=True,premultiplied=self.companions[1].alpha_conversion_offloaded)
                    else:shutil.copyfile(foreground,stage/'foreground.mov')
                    # Bundle is new and uniquely named. Its source media is never overwritten by export.
                    os.replace(stage,bundle)
                data=self.processing_result['alignment']
                data['source']=dict(scene=str(bundle/'scene.mp4'),foreground=str(bundle/'foreground.mov'),
                    original=str(bundle/'capture.mp4'),foreground_premultiplied=self.companions[1].alpha_conversion_offloaded,fps=self.fps,width=self.width,height=self.height,audio=self.microphone)
                data['processing_directory']=str(self.processing_session.directory)
                data['render_project']=self.render_project
                data['output_stem']=str(self.path.with_suffix(''))
                atomic_json(self.path.with_suffix('.alignment.json'),data)
                self.awaiting_review=True
                return
            # Stage beside destination so recordings on another drive also
            # publish atomically; failed copies never appear as finished MP4s.
            fd, staged = tempfile.mkstemp(prefix='.' + self.path.stem + '-', suffix='.partial', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'wb') as output, Path(selected).open('rb') as source:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(staged, self.path)
            finally:
                Path(staged).unlink(missing_ok=True)
            if 'alignment' in self.processing_result:
                from update_core import atomic_json
                atomic_json(self.path.with_suffix('.alignment.json'), self.processing_result['alignment'])
        except Exception as exc:
            self.error = str(exc)
            audio_worker=getattr(self,'_audio_worker',None)
            if audio_worker is not None and audio_worker.is_alive():self._retain_live_audio=True
            elif hasattr(self,'_audio_file'):
                try:self._audio_file.close()
                except OSError:pass
            # Release a blocked encoder pipe before attempting recovery/cleanup.
            if self._video is not None and self._video.poll() is None:
                self._video.kill()
                self._video.wait(timeout=5)
            writer=getattr(self,'_writer',None)
            if writer is not None and writer.is_alive():writer.join(timeout=2)
            if writer is not None and writer.is_alive():self._retain_live_audio=True
            # If analysis/source retention fails after capture encoding, recover the playable take.
            candidate=locals().get('ready')
            if candidate is None and self.packed and getattr(self,'_packed_source',Path('missing')).is_file():
                recovery=self.path.with_suffix('.recovery.mkv')
                try:
                    try:os.replace(self._packed_source,recovery)
                    except OSError:
                        from edit_pipeline import publish_file
                        publish_file(self._packed_source,recovery)
                    from update_core import atomic_json
                    atomic_json(recovery.with_suffix('.json'),dict(width=self.width,height=self.height,fps=self.fps,
                        planes=['captured','background','foreground'],color_stream=0,alpha_stream=1,
                        color_codec='h264_yuv420p_crf23',premultiplied=True,error=self.error))
                    if self._wave.exists() and not getattr(self,'_retain_live_audio',False):
                        audio_recovery=self.path.with_suffix('.recovery.wav')
                        from edit_pipeline import publish_file
                        publish_file(self._wave,audio_recovery)
                    self.error+='; layered source recovered at '+str(recovery)
                except OSError:
                    self.error+='; temporary layered source retained at '+str(self._packed_source)
                    self._temp._finalizer.detach();self._temp=None

            if candidate is not None and Path(candidate).is_file() and not self.path.exists():
                try:
                    from edit_pipeline import publish_file
                    publish_file(candidate,self.path)
                    self.error+='; captured take recovered at '+str(self.path)
                except OSError:pass
        finally:
            try:
                if self.processing_session:
                    self.processing_session.finish('failed' if self.error or self.processing_error else ('review' if self.awaiting_review else 'complete'))
                if token is not None:_current.reset(token)
                if self.frame_count:
                    self.report_path.write_text(json.dumps(self.diagnostic_report(), indent=2), encoding='utf-8')
            except Exception:
                # Diagnostics must never prevent finalizing an otherwise valid MP4.
                pass
            if self._temp is not None:
                if getattr(self,'_retain_live_audio',False):
                    self.error+='; audio workspace retained at '+self._temp.name
                    self._temp._finalizer.detach()
                else:
                    try:self._temp.cleanup()
                    except OSError as cleanup_error:
                        self._temp._finalizer.detach()
                        self.error=(self.error or '')+'; workspace retained: '+str(cleanup_error)
            self.finished = True

    def abort(self):
        """Abandon capture without muxing, diagnostics, or touching the destination."""
        self.cancelled = True
        self.stopping = True
        errors = []
        def attempt(operation):
            try:
                operation()
            except Exception as exc:
                errors.append(str(exc))
        for companion in self.companions:
            attempt(companion.abort)
        if self._stream is not None:
            attempt(self._stream.stop)
            attempt(self._stream.close)
        if self._video is not None:
            attempt(self._video.kill)
            attempt(lambda: self._video.wait(timeout=5))
        # Wake an idle writer; killing FFmpeg also releases a blocked pipe write.
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                break
        self._frames.put_nowait(None)
        workers = (getattr(self, '_writer', None), self._audio_worker)
        for worker in workers:
            if worker is not None:
                attempt(lambda w=worker: w.join(timeout=10))
        if any(w is not None and w.is_alive() for w in workers):
            errors.append('Recording worker did not stop; temporary files retained')
        else:
            if hasattr(self, '_audio_file'):
                attempt(self._audio_file.close)
            if self._video is not None:
                for pipe in (self._video.stdin, self._video.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except (OSError, ValueError):
                            # FFmpeg was deliberately killed; closing a broken
                            # buffered input pipe is expected during discard.
                            pass
            if self._temp is not None:
                attempt(self._temp.cleanup)
        watchdog=getattr(self,'_watchdog',None)
        if watchdog is not None:watchdog.join(timeout=1)
        attempt(lambda:self.path.with_suffix('.stall.json').unlink(missing_ok=True))
        if errors:
            raise RuntimeError('; '.join(errors))
        self.finished = True

    def discard_async(self):
        """Choose discard only while capturing; Stop/Save owns finalization afterward."""
        if self.stopping:
            return False
        self.cancelled = True
        self.stopping = True
        def discard():
            try:
                self.abort()
            except Exception as exc:
                self.error = 'Discard cleanup failed: ' + str(exc)
            finally:
                # Publish completion after the error, so the UI cannot report
                # success before learning that cleanup failed.
                self.finished = True
        threading.Thread(target=discard, daemon=True).start()
        return True

