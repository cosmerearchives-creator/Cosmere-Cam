"""Published-release UI integration probe (run only by windows_e2e.py).

Version is a previous-version fixture; all network, progress signals, busy gating,
process wait, extraction, pointer commit and relaunched frozen application are real.
Only the test restart command adds --smoke-test so CI doesn't leave a GUI running.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dynamic_teleprompter'))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
import update_ui
from update_core import atomic_json


def main():
    root = Path(sys.argv[1])
    os.environ['COSMERE_INSTALL_ROOT'] = str(root)
    os.environ.pop('COSMERE_GITHUB_TOKEN', None)
    os.environ.pop('COSMERE_TEST_MODE', None)
    update_ui.VERSION = '0.0.0'  # Previous-version protocol fixture, never a shipped override.
    app = QApplication([])
    real_popen = update_ui.subprocess.Popen
    def test_restart(command, **kwargs):
        return real_popen([*command, '--smoke-test'], **kwargs)
    class Window(QMainWindow):
        recorder = object()  # Simulate active capture when the update is offered.
        audio_monitor = voice_worker = test_voice_worker = downloader = None
        def closeEvent(self, event):
            self.updates.on_clean_close()
            event.accept()
    window = Window()
    window.updates = update_ui.Updates(window)
    window.setCentralWidget(window.updates.page)
    window.show()
    progress = []
    window.updates.progress.connect(progress.append)
    def ready(pending):
        window.updates.restart()
        assert window.isVisible(), 'Recording was interrupted'
        assert not window.updates.restart_requested
        window.recorder = None
        atomic_json(root / 'controller-verification.json', {
            'automatic_startup_check': True, 'download_progress': bool(progress),
            'recording_deferred': True, 'version': pending['version'],
            'notes_displayed': bool(window.updates.status.text())})
        window.updates.restart()
    window.updates.downloaded.connect(ready)
    QTimer.singleShot(180_000, lambda: app.exit(2))
    with patch('update_ui.subprocess.Popen', side_effect=test_restart), patch('update_ui.read_token', return_value=''):
        return app.exec()


if __name__ == '__main__':
    sys.exit(main())
