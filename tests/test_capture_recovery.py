"""Regression coverage for compact editable capture and bounded plugin transport."""
import os,sys,threading,queue,time,tempfile,wave,unittest
from pathlib import Path
from unittest.mock import patch,Mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dynamic_teleprompter'))
from PySide6.QtGui import QImage,QColor
from recorder import CanvasRecorder
from vst2_host import Host

class FfmpegDiscovery(unittest.TestCase):
    def test_successful_lookup_is_cached_by_configured_path(self):
        import recorder
        with patch.dict(recorder._ffmpeg_paths,{},clear=True),patch('recorder._locate_ffmpeg',side_effect=['first.exe','second.exe']) as locate:
            self.assertEqual(recorder.resolve_ffmpeg('first'),'first.exe')
            self.assertEqual(recorder.resolve_ffmpeg('first'),'first.exe')
            self.assertEqual(recorder.resolve_ffmpeg('second'),'second.exe')
            self.assertEqual(locate.call_count,2)
    def test_explicit_executable_does_not_scan_path(self):
        import recorder
        with tempfile.TemporaryDirectory() as folder:
            executable=Path(folder)/'ffmpeg.exe';executable.write_bytes(b'fixture')
            with patch('recorder.shutil.which',side_effect=AssertionError('unexpected PATH scan')):
                self.assertEqual(recorder.resolve_ffmpeg(str(executable)),str(executable.resolve()))

class PluginDeadlines(unittest.TestCase):
    def host(self):
        host=Host.__new__(Host)
        host.lock=threading.RLock();host.replies=queue.Queue(maxsize=4);host.requests=queue.Queue(maxsize=1)
        host.process=Mock();host.process.poll.return_value=None
        return host
    def test_blocked_pipe_write_cannot_trap_audio_request(self):
        host=self.host();release=threading.Event()
        host.process.stdin.write.side_effect=lambda payload:release.wait(3)
        def kill():host.process.poll.return_value=1;release.set()
        host.process.kill.side_effect=kill
        writer=threading.Thread(target=host._write,daemon=True);writer.start()
        start=time.monotonic()
        with self.assertRaisesRegex(RuntimeError,'timed out'):host.request(1,b'pcm',timeout=.05)
        self.assertLess(time.monotonic()-start,.5)
        host.process.kill.assert_called_once();writer.join(1);self.assertFalse(writer.is_alive())
    def test_contended_request_lock_is_bounded(self):
        host=self.host();ready=threading.Event();release=threading.Event()
        def hold():
            with host.lock:ready.set();release.wait(3)
        worker=threading.Thread(target=hold);worker.start();ready.wait(1)
        try:
            start=time.monotonic()
            with self.assertRaisesRegex(RuntimeError,'timed out'):host.request(1,b'pcm',timeout=.05)
            self.assertLess(time.monotonic()-start,.5)
            host.process.kill.assert_called_once()
        finally:release.set();worker.join(1)

class CompactTransport(unittest.TestCase):
    def test_half_size_transport_keeps_mask_bytes_separate_from_color(self):
        import numpy as np
        from capture_planes import planes
        colors=[]
        for color in ('red','green','blue'):
            image=QImage(16,16,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor(color));colors.append(image)
        alpha=np.arange(256,dtype=np.uint8).reshape(16,16)
        pixels=np.frombuffer(colors[2].bits(),dtype=np.uint8).reshape(16,16,4)
        pixels[:,:,:3]=0;pixels[:,:,3]=alpha
        payload=b''.join(planes(colors));size=256
        self.assertEqual(len(payload),size*6) # Previously twelve bytes per pixel.
        self.assertEqual(payload[3*size:4*size],alpha.tobytes())
        self.assertEqual(payload[4*size+3*size//4:4*size+size],bytes([128])*(size//4))
        self.assertEqual(payload[-size//4:],bytes([128])*(size//4))
        self.assertTrue(all(value==82 for value in payload[:size])) # Limited-range BT.601 red.
        self.assertEqual(pixels[:,:,3].tobytes(),alpha.tobytes())

class Recovery(unittest.TestCase):
    def test_preparation_failure_retains_closed_audio_and_video(self):
        with tempfile.TemporaryDirectory() as folder:
            r=CanvasRecorder(Path(folder)/'take.mp4',64,64,10,microphone=False,editable=True);r.start()
            with wave.open(str(r._wave),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000);wav.writeframes(bytes(9600))
            image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('blue'))
            r.push_frame(image,image,image)
            with patch('edit_pipeline.run_media',side_effect=RuntimeError('preparation failed')):
                r.finish_async();deadline=time.monotonic()+15
                while not r.finished and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(r.finished);self.assertIn('preparation failed',r.error)
            self.assertTrue(r.path.with_suffix('.recovery.mkv').is_file())
            with wave.open(str(r.path.with_suffix('.recovery.wav')),'rb') as wav:self.assertEqual(wav.getnframes(),4800)
            self.assertFalse(r.path.exists())
    def test_compact_capture_preserves_exact_alpha_mask(self):
        import numpy as np,subprocess
        from recorder import resolve_ffmpeg
        with tempfile.TemporaryDirectory() as folder:
            r=CanvasRecorder(Path(folder)/'mask.mp4',64,64,10,microphone=False,editable=True);r.start()
            image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor(0,0,0,0))
            pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(64,64,4)
            expected=np.tile(np.arange(64,dtype=np.uint8)*4,(64,1));pixels[:,:,3]=expected
            r.push_frame(image,image,image)
            with patch('edit_pipeline.run_media',side_effect=RuntimeError('retain packed fixture')):
                r.finish_async();deadline=time.monotonic()+15
                while not r.finished and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(r.finished)
            result=subprocess.run([resolve_ffmpeg(),'-v','error','-i',str(r.path.with_suffix('.recovery.mkv')),'-map','0:v:1','-frames:v','1','-f','rawvideo','-pix_fmt','gray','pipe:1'],capture_output=True,check=True,timeout=15)
            self.assertEqual(result.stdout,expected.tobytes())

    def test_watchdog_persists_stall_evidence_before_shutdown(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            r=CanvasRecorder(Path(folder)/'stalled.mp4',64,64,10,microphone=False)
            r._encoder_pending_since=time.monotonic()-3
            worker=threading.Thread(target=r._watch_workers);worker.start()
            target=r.path.with_suffix('.stall.json');deadline=time.monotonic()+2
            try:
                while not target.exists() and time.monotonic()<deadline:time.sleep(.01)
                data=json.loads(target.read_text())
                self.assertGreater(data['worker_stalls'][0]['waits']['encoder'],2)
                self.assertTrue(data['worker_stalls'][0]['threads'])
            finally:r.finished=True;worker.join(1)
            r.abort();self.assertFalse(target.exists())

    def test_timing_catchup_is_one_batch_with_prior_frame_then_latest(self):
        import io
        from types import SimpleNamespace
        r=CanvasRecorder('unused.mp4',4,4,30,microphone=False)
        previous=QImage(4,4,QImage.Format.Format_ARGB32_Premultiplied);previous.fill(QColor('blue'))
        latest=QImage(4,4,previous.format());latest.fill(QColor('red'));r._last_bytes=previous
        with patch.object(r,'frames_due',return_value=31):r.push_frame(latest)
        self.assertIsNone(r.error);self.assertEqual(r._frames.qsize(),1);self.assertEqual(r.frame_count,31)
        output=io.BytesIO();r._video=SimpleNamespace(stdin=output);r._frames.put(None);r._write_frames()
        payload=output.getvalue();self.assertEqual(r.encoder_writes,31);self.assertEqual(len(payload),31*64)
        self.assertEqual(payload[:4],bytes([255,0,0,255]));self.assertEqual(payload[-4:],bytes([0,0,255,255]))

    def test_queue_pressure_keeps_duration_and_latest_frame_with_bounded_memory(self):
        import io
        from types import SimpleNamespace
        r=CanvasRecorder('unused.mp4',4,4,30,microphone=False);r._frames=queue.Queue(maxsize=2)
        with patch.object(r,'frames_due',return_value=1):
            for index in range(8):
                image=QImage(4,4,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor(index*20,0,0));r.push_frame(image)
        self.assertIsNone(r.error);self.assertEqual(r._frames.qsize(),2);self.assertGreater(r.queue_batches_coalesced,0)
        output=io.BytesIO();r._video=SimpleNamespace(stdin=output)
        reader=threading.Thread(target=r._write_frames);reader.start();r._frames.put(None);reader.join(2)
        self.assertFalse(reader.is_alive());self.assertEqual(len(output.getvalue()),8*64)
        self.assertEqual(output.getvalue()[-4:],bytes([0,0,140,255]))
        self.assertLess(r.encoded_unique_frames,8)

    def test_pause_rejects_capture_without_waiting_for_device_close(self):
        r=CanvasRecorder('unused.mp4',64,64,10);r._started=time.monotonic();r._stream=Mock()
        r.pause_capture();self.assertTrue(r.stopping);r._stream.stop.assert_not_called()

if __name__=='__main__':unittest.main()
