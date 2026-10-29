"""Run with: python -m unittest discover -s tests -v (Qt offscreen)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dynamic_teleprompter'))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from app import ReaderWindow, MainWindow
from recorder import CanvasRecorder


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, self.settings_dir.name)
        self.settings_patch = patch('app.QSettings', side_effect=lambda *args: QSettings(
            str(Path(self.settings_dir.name) / 'reader.ini'), QSettings.Format.IniFormat))
        self.settings_patch.start()
        self.reader = ReaderWindow()
        self.reader.show()
        self.reader.set_script(' '.join('word%03d' % i for i in range(250)))
        self.qt.processEvents()

    def tearDown(self):
        self.reader.close()
        self.reader.deleteLater()
        self.qt.processEvents()
        self.settings_patch.stop()
        self.settings_dir.cleanup()

    def test_font_size_persists(self):
        self.assertEqual(self.reader.font_size_control.value(), 36)
        self.reader.font_size_control.setValue(48)
        other = ReaderWindow()
        self.assertEqual(other.font_size_control.value(), 48)
        other.close()

    def test_progress_moves_within_line_without_replacing_document(self):
        r = self.reader
        original = r.view.toPlainText()
        goals = []
        for i in range(40, 60):
            r.set_progress(i)
            goals.append(r._scroll_goal)
        self.assertTrue(all(b > a for a, b in zip(goals, goals[1:])))
        self.assertEqual(original, r.view.toPlainText())
        r._last_tick = time.monotonic() - .016
        r._animate_scroll()
        self.assertGreater(r._scroll_position, 0)
        self.assertLess(r._scroll_position, r._scroll_goal)

    def test_backward_correction_reset_resize_and_completion(self):
        r = self.reader
        r.set_progress(100)
        goal = r._scroll_goal
        r.set_progress(40)
        self.assertLess(r._scroll_goal, goal)
        r.resize(850, 650)
        self.qt.processEvents()
        r._animate_scroll()
        self.assertLessEqual(r._scroll_goal, r.view.verticalScrollBar().maximum())
        r.set_progress(250)
        self.assertEqual(r.status.text(), 'SCRIPT COMPLETE')
        r.set_script('')
        self.assertEqual(r._scroll_position, 0)


class DiscardTests(unittest.TestCase):
    def test_discard_wakes_idle_writer_and_skips_all_save_work(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'existing.mp4'
            output.write_bytes(b'previous take')
            r = CanvasRecorder(output, 2, 2, 30, microphone=False)
            r._temp = tempfile.TemporaryDirectory()
            temp_path = Path(r._temp.name)
            (temp_path / 'video.mp4').write_bytes(b'incomplete')
            r._video = Mock()
            r._writer = threading.Thread(target=r._write_frames)
            r._writer.start()
            with patch.object(r, '_finish') as finish, patch('recorder.subprocess.run') as mux:
                self.assertTrue(r.discard_async())
                r.finish_async()
                self.assertFalse(r.discard_async())
                deadline = time.monotonic() + 3
                while not r.finished and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(r.finished)
                self.assertFalse(r._writer.is_alive())
                self.assertFalse(temp_path.exists())
                self.assertIsNone(r.error)
                self.assertFalse(r.report_path.exists())
                self.assertEqual(output.read_bytes(), b'previous take')
                finish.assert_not_called()
                mux.assert_not_called()

    def test_stop_keeps_save_path_and_prevents_late_discard(self):
        r = CanvasRecorder('unused.mp4', 2, 2, 30, microphone=False)
        called = threading.Event()
        with patch.object(r, '_finish', side_effect=called.set):
            r.finish_async()
            self.assertTrue(called.wait(1))
            self.assertFalse(r.discard_async())
            self.assertFalse(r.cancelled)

    def test_ui_decline_keeps_capture_running(self):
        ui = Mock()
        ui.recorder.stopping = False
        with patch('app.QMessageBox') as message:
            message.return_value.exec.return_value = message.StandardButton.Cancel
            MainWindow._discard_recording(ui)
        ui.recorder.discard_async.assert_not_called()
        ui.record_timer.stop.assert_not_called()

    def test_ui_confirm_uses_discard_without_stop_save(self):
        ui = Mock()
        ui.recorder.stopping = False
        with patch('app.QMessageBox') as message:
            message.return_value.exec.return_value = message.StandardButton.Discard
            MainWindow._discard_recording(ui)
        ui.recorder.discard_async.assert_called_once()
        ui.record_timer.stop.assert_called_once()
        ui._stop_recording.assert_not_called()

    def test_normal_finish_copies_video_and_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            r = CanvasRecorder(Path(directory) / 'saved.mp4', 2, 2, 30, microphone=False)
            r._temp = tempfile.TemporaryDirectory()
            r._silent = Path(r._temp.name) / 'video.mp4'
            r._silent.write_bytes(b'encoded video')
            r.frame_count = 1
            r._writer = Mock()
            r._writer.is_alive.return_value = False
            r._video = Mock()
            r._video.wait.return_value = 0
            r._video.stderr.read.return_value = b''
            r._finish()
            self.assertIsNone(r.error)
            self.assertEqual(r.path.read_bytes(), b'encoded video')
            self.assertTrue(r.report_path.exists())


if __name__ == '__main__':
    unittest.main()

