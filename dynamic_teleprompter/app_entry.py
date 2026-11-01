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

from PySide6.QtCore import QTimer
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
                from app import ContinuousVideoDecoder
                decoder = ContinuousVideoDecoder.__new__(ContinuousVideoDecoder)
                decoder.path, decoder.ffmpeg = str(saved), resolve_ffmpeg()
                fps, width, height = decoder._metadata()
                if (width, height) != (48, 64):
                    raise RuntimeError('Video layer metadata lost portrait geometry')
            report = os.environ.get('COSMERE_SMOKE_REPORT')
            if report:
                atomic_json(report, {'version': VERSION, 'qt': True, 'ffmpeg': bool(resolve_ffmpeg()),
                                     'discard': True, 'save': True, 'reader_size': window.reader.font_size_control.value()})
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
