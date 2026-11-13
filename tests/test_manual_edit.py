"""Regressions for manual cuts and full written captions."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dynamic_teleprompter'))

from manual_edit import caption_groups, detect_cough_candidates, full_script_events, kept_ranges
from recording_processing import align_script


class ManualEditTests(unittest.TestCase):
    def test_overlapping_manual_cuts_join_and_snap(self):
        data = dict(duration=6., source=dict(fps=30))
        cuts = [dict(start=0., end=1.01), dict(start=2., end=3.), dict(start=2.5, end=4.)]
        self.assertEqual(kept_ranges(data, cuts), [(1., 2.), (4., 6.)])

    def test_script_survives_paraphrase_and_missing_lines(self):
        script = 'The first point. An essential explanation. The final point.'
        data = align_script(script, [dict(word='final', start=2.9, end=2.95)], 3.)
        data['render_project'] = dict(groups=['The first point.', 'An essential explanation.', 'The final point.'])
        groups = caption_groups(data)
        events = full_script_events(data, groups)
        self.assertEqual(' '.join(text for _, text, _ in events if text), script)
        self.assertLess(events[-1][0], 3.)
        self.assertTrue(any(word['needs_approval'] for word in data['words']))

    def test_stale_groups_cannot_drop_script_words(self):
        data = dict(script='First and second.', render_project=dict(groups=['First']))
        self.assertEqual(caption_groups(data), ['First and second.'])

    def test_noise_burst_is_a_suggestion_and_speech_is_excluded(self):
        import numpy as np
        samples = np.zeros(32000, dtype=np.int16)
        samples[8000:11200] = np.random.default_rng(42).integers(-10000, 10000, 3200)
        self.assertEqual(len(detect_cough_candidates(samples, 16000, [])), 1)
        self.assertEqual(detect_cough_candidates(samples, 16000, [dict(start=.48, end=.72)]), [])

    def test_saved_take_exports_manual_cut_with_full_script(self):
        import tempfile
        import threading
        import time
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QApplication
        import imageio_ffmpeg
        from export_ui import ExportDialog
        from manual_edit import prepare_captions
        from recorder import CanvasRecorder, resolve_processing_ffmpeg

        qt = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'take.mp4'
            recorder = CanvasRecorder(path, 160, 120, 10, microphone=False, editable=True)
            recorder.script = 'First point. Essential detail. Final point.'
            recorder.render_project = dict(groups=['First point.', 'Essential detail.', 'Final point.'],
                                           settings=dict(font='Arial', font_size=16, chars=24, lines=2,
                                                         media_layers=[dict(type='text', visible=True)]))
            recorder.processing_options = dict(manual_review=True)
            recorder.start()
            original = QImage(160, 120, QImage.Format.Format_ARGB32_Premultiplied)
            original.fill(QColor('red'))
            scene = QImage(original)
            scene.fill(QColor('blue'))
            foreground = QImage(original)
            foreground.fill(QColor(0, 0, 0, 0))
            tick = [0.]
            recorder.elapsed = lambda: tick[0]
            for index in range(20):
                tick[0] = (index + .1) / 10
                recorder.push_frame(original, scene, foreground)
                time.sleep(.015)
            recorder.finish_async()
            deadline = time.monotonic() + 20
            while not recorder.finished and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(recorder.finished)
            self.assertIsNone(recorder.error)
            saved = path.read_bytes()
            review = recorder.processing_result['manual_review']
            work = root / 'work'
            work.mkdir()
            aligned = prepare_captions(review, [dict(start=.5, end=1.)],
                                       resolve_processing_ffmpeg(), work, threading.Event(), lambda _: None)
            self.assertEqual(' '.join(word['word'] for word in aligned['words']), recorder.script)
            for word in aligned['words']:
                word['needs_approval'] = False
            dialog = ExportDialog(None, aligned, work / 'caption-review.json')
            dialog.show()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                qt.processEvents()
                if dialog.cancel_button.text() == 'Close':
                    break
                time.sleep(.01)
            self.assertIn('exports', aligned, dialog.status.text())
            reader = imageio_ffmpeg.read_frames(aligned['exports']['edited'], pix_fmt='rgb24')
            next(reader)
            self.assertEqual(sum(1 for _ in reader), 15)
            reader.close()
            self.assertEqual(path.read_bytes(), saved)
            self.assertFalse((root / 'take_baseline.mp4').exists())
            dialog.close()
            qt.processEvents()
