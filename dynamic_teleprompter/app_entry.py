"""Frozen entry point: divert VST child processes before importing Qt."""
import multiprocessing
multiprocessing.freeze_support()
import json
import os
import sys
from pathlib import Path

if '--smoke-test' in sys.argv:
    os.environ['COSMERE_TEST_MODE'] = '1'
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer,QUrl
from PySide6.QtWidgets import QApplication
from app import MainWindow
from version import VERSION
from update_core import atomic_json
from theme import apply_theme


def run():
    application = QApplication(sys.argv)
    apply_theme(application)
    smoke = '--smoke-test' in sys.argv
    if smoke:
        MainWindow.prompt_model_if_missing = lambda self: None
    window = MainWindow()
    window.show()
    def complete_startup():
        root = os.environ.get('COSMERE_INSTALL_ROOT')
        token = os.environ.get('COSMERE_BOOT_TOKEN')
        if root and not smoke:
            # Alpha17 launchers are intentionally stable; migrate only the
            # Start-menu branding after a healthy newer application starts.
            import threading
            def brand_shortcut():
                try:
                    from windows_launcher import shortcut
                    shortcut(Path(root))
                except Exception:
                    pass
            threading.Thread(target=brand_shortcut, daemon=True).start()
        if smoke:
            plugin_folder = os.environ.get('COSMERE_REAPLUGS_TEST_DIR')
            if plugin_folder:
                import vst2_host
                plugin = next(Path(plugin_folder).rglob('reaeq-standalone.dll'))
                host = vst2_host.Host(plugin)
                try:
                    if len(host.audio(bytes(1024))) != 1024:
                        raise RuntimeError('Frozen VST2 processing failed')
                    host.restore(host.capture())
                finally:
                    host.close()
            alignment_model = os.environ.get('COSMERE_ALIGNMENT_TEST_MODEL')
            if alignment_model:
                from vosk import Model, KaldiRecognizer, SetLogLevel
                from recording_processing import align_script
                from processing_ui import ProcessingChoices, AlignmentReview
                SetLogLevel(-1)
                recognizer = KaldiRecognizer(Model(alignment_model), 16000)
                recognizer.SetWords(True)
                recognizer.AcceptWaveform(bytes(32000))
                heard = json.loads(recognizer.FinalResult()).get('result', [])
                aligned = align_script('Every word stays visible.', heard, 1)
                if len(aligned['words']) != 4:
                    raise RuntimeError('Frozen full-script alignment failed')
            # Exercise actual frozen FFmpeg capture and discard without devices.
            import tempfile
            import time
            from recorder import CanvasRecorder, resolve_ffmpeg
            from PySide6.QtGui import QImage
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'smoke.mp4'
                recorder = CanvasRecorder(path, 64, 64, 30, microphone=False)
                recorder.start()
                image = QImage(64, 64, QImage.Format.Format_RGBA8888)
                image.fill(0)
                recorder.push_frame(image)
                recorder.discard_async()
                deadline = time.monotonic() + 15
                while not recorder.finished and time.monotonic() < deadline:
                    time.sleep(.05)
                if not recorder.finished or recorder.error or path.exists():
                    raise RuntimeError('Frozen recording discard failed')
                saved = Path(directory) / 'saved.mp4'
                recorder = CanvasRecorder(saved, 48, 64, 30, microphone=False)
                recorder.start()
                image = QImage(48, 64, QImage.Format.Format_RGBA8888)
                image.fill(0)
                recorder.push_frame(image)
                recorder.finish_async()
                deadline = time.monotonic() + 20
                while not recorder.finished and time.monotonic() < deadline:
                    time.sleep(.05)
                if not recorder.finished or recorder.error or not saved.is_file():
                    raise RuntimeError('Frozen recording save failed: ' + str(recorder.error))
                import subprocess
                subprocess.run([resolve_ffmpeg(), '-v', 'error', '-i', str(saved), '-f', 'null', '-'],
                               check=True, capture_output=True, timeout=10)
                layered=CanvasRecorder(Path(directory)/'layered.mp4',64,64,30,microphone=False,editable=True)
                layered.start()
                layer_image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);layer_image.fill(0xff203040)
                layered.push_frame(layer_image,layer_image,layer_image);layered.finish_async()
                deadline=time.monotonic()+25
                while not layered.finished and time.monotonic()<deadline:time.sleep(.02)
                if not layered.finished or layered.error or not layered.path.is_file():
                    raise RuntimeError('Frozen layered capture failed: '+str(layered.error))
                # Exercise the packaged Qt caption renderer and FFmpeg export, not just source imports.
                from edit_pipeline import run_media
                from export_ui import ExportDialog
                from recording_processing import align_script
                from PySide6.QtMultimedia import QMediaPlayer
                from PySide6.QtMultimediaWidgets import QVideoWidget
                media_probe=QMediaPlayer()
                clean=Path(directory)/'clean.mp4'
                run_media([resolve_ffmpeg(),'-y','-v','error','-stream_loop','-1','-i',str(saved),
                           '-t','0.4','-vf','scale=64:64','-c:v','libx264',str(clean)])
                video_probe=QVideoWidget();media_probe.setVideoOutput(video_probe)
                media_probe.setSource(QUrl.fromLocalFile(str(clean)))
                deadline=time.monotonic()+10
                while media_probe.mediaStatus() in (QMediaPlayer.MediaStatus.LoadingMedia,QMediaPlayer.MediaStatus.NoMedia) and time.monotonic()<deadline:
                    application.processEvents();time.sleep(.01)
                if media_probe.error()!=QMediaPlayer.Error.NoError or media_probe.mediaStatus()==QMediaPlayer.MediaStatus.InvalidMedia:
                    raise RuntimeError('Frozen join preview decoder failed: '+media_probe.errorString())
                media_probe.stop();media_probe.setSource(QUrl());application.processEvents()
                approved=align_script('Verified export',[],.4)
                for word in approved['words']:word['needs_approval']=False
                approved.update(source=dict(scene=str(clean),width=64,height=64,fps=30,audio=False),
                    render_project=dict(groups=['Verified export'],settings=dict(font_size=10,foreground='#FFFFFF')),
                    output_stem=str(Path(directory)/'approved'),advanced_mode='assisted',cuts=[])
                export=ExportDialog(window,approved,Path(directory)/'review.alignment.json')
                deadline=time.monotonic()+30
                while 'exports' not in approved and time.monotonic()<deadline:
                    application.processEvents();time.sleep(.01)
                    if export.worker is not None and not export.worker.isRunning() and 'Export stopped' in export.status.text():break
                if 'exports' not in approved:raise RuntimeError('Frozen approved export failed: '+export.status.text())
                while export.worker.isRunning():application.processEvents();time.sleep(.01)
                application.processEvents();export.close()
                if not Path(approved['exports']['baseline']).is_file() or not Path(approved['exports']['edited']).is_file():
                    raise RuntimeError('Frozen comparison exports missing')
                from app import ContinuousVideoDecoder
                decoder = ContinuousVideoDecoder.__new__(ContinuousVideoDecoder)
                decoder.path, decoder.ffmpeg = str(saved), resolve_ffmpeg()
                fps, width, height = decoder._metadata()
                if (width, height) != (48, 64):
                    raise RuntimeError('Video layer metadata lost portrait geometry')
            report = os.environ.get('COSMERE_SMOKE_REPORT')
            if report:
                atomic_json(report, {'version': VERSION, 'qt': True, 'ffmpeg': bool(resolve_ffmpeg()),
                                     'discard': True, 'save': True, 'approved_export':True, 'editable_capture':True, 'reader_size': window.reader.font_size_control.value()})
            # Avoid mutating user settings or triggering save prompts in CI.
            application.quit()
        if root and token and len(token) == 32 and token.isalnum():
            atomic_json(Path(root) / ('boot-' + token + '.json'), {'token': token, 'version': VERSION})
    def ready():
        try:
            complete_startup()
        except Exception as error:
            report = os.environ.get('COSMERE_SMOKE_REPORT')
            if report:
                atomic_json(report, {'error': str(error)})
            application.exit(1)
    QTimer.singleShot(0, ready)
    return application.exec()


if __name__ == '__main__':
    sys.exit(run())
