"""Regression: keyboard movement must render without a new text/settings event."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dynamic_teleprompter'))
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
import app


class MovementRenderingTests(unittest.TestCase):
    def test_unchanged_text_keeps_live_position_and_head_turn(self):
        qt = QApplication.instance() or QApplication([])
        observed = []
        def marker(painter, layer, size):
            observed.append((layer.get('x', 0), layer.get('_movement_turn', 0)))
            painter.fillRect(round(size[0] * (.5 + layer.get('x', 0) / 100)),
                             round(size[1] * .75), 12, 12, QColor('#ff0000'))
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(patch('app.SETTINGS_PATH', root/'missing.json'))
            stack.enter_context(patch('app.QSettings', side_effect=lambda *args:
                QSettings(str(root/'settings.ini'), QSettings.Format.IniFormat)))
            stack.enter_context(patch.object(app.MainWindow, 'prompt_model_if_missing'))
            stack.enter_context(patch.object(app.MainWindow, 'arrange_windows'))
            stack.enter_context(patch.object(app.lip_microphone, 'start'))
            stack.enter_context(patch('app.draw_live2d', side_effect=marker))
            window = app.MainWindow()
            try:
                window.canvas_resolution.addItem('320x240')
                window.canvas_resolution.setCurrentText('320x240')
                layer = dict(type='live2d', path='synthetic-only', visible=True, x=0, y=0,
                             movement=dict(enabled=True))
                window.media_layers = [dict(type='text', visible=True), layer]
                window.script.setPlainText('This text never changes during movement.')
                window.animation_global = {**app.ANIMATION_DEFAULT, 'intro':'None', 'outro':'None'}
                window.prepare_groups()
                window.next()
                window.out.render_frame(force=True)
                label = window.out.labels[0]
                unchanged = (label.text(), label._phase_started)
                observed.clear()
                window._movement_keys = {'d'}
                now = time.monotonic()
                window._movement_last_tick = now
                frames = []
                with patch.object(QApplication, 'activeWindow', return_value=window.out), \
                     patch.object(window, 'render', wraps=window.render) as text_render:
                    for step in range(1, 21):
                        with patch('app.time.monotonic', return_value=now + step / 30):
                            window._tick_movement()
                            frames.append(window.out.render_frame().copy())
                    text_render.assert_not_called()
                self.assertGreater(layer['x'], 1)
                self.assertAlmostEqual(observed[-1][0], layer['x'])
                self.assertGreater(observed[-1][1], .5)
                self.assertNotEqual(frames[0], frames[-1])
                self.assertEqual((label.text(), label._phase_started), unchanged)
                # Saving a project still strips transient pose/velocity state.
                saved = window.settings()['media_layers'][1]
                self.assertNotIn('_movement_turn', saved)
                self.assertNotIn('_movement_vertical', saved)
                # Head returns smoothly while translation settles without text events.
                turn = layer['_movement_turn']
                window._movement_keys.clear()
                with patch.object(QApplication, 'activeWindow', return_value=window.out):
                    for step in range(21, 111):
                        with patch('app.time.monotonic', return_value=now + step / 30):
                            window._tick_movement()
                            window.out.render_frame()
                self.assertLess(abs(layer['_movement_turn']), abs(turn) / 100)
                self.assertAlmostEqual(observed[-1][0], layer['x'])
                self.assertEqual((label.text(), label._phase_started), unchanged)
                # Clear/awaiting-first output also binds the current live layer list.
                replacement = dict(layer, x=-15)
                window.media_layers = [dict(type='text', visible=True), replacement]
                window.clear_output()
                window.out.render_frame(force=True)
                self.assertEqual(observed[-1][0], -15)
            finally:
                window._saved_project_snapshot = window._project_snapshot()
                window.close()
                qt.processEvents()
