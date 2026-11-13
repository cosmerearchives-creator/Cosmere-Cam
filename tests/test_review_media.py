import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dynamic_teleprompter'))

from recorder import resolve_processing_ffmpeg
from review_media import build_preview, build_waveform


class ReviewMediaTests(unittest.TestCase):
    def test_portrait_preview_keeps_frames_and_waveform_time(self):
        from imageio_ffmpeg import read_frames
        ffmpeg = resolve_processing_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            source = str(Path(folder) / 'source.mp4')
            subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=540x960:rate=30:duration=2',
                            '-f', 'lavfi', '-i', 'aevalsrc=if(lt(t\\,1)\\,0\\,0.6*sin(440*2*PI*t)):s=8000:d=2',
                            '-c:v', 'libx264', '-threads:v', '2', '-c:a', 'aac', source],
                           capture_output=True, check=True, timeout=20)
            cancel = threading.Event()
            preview = build_preview(ffmpeg, source, folder, cancel)
            reader = read_frames(str(preview))
            try:
                info = next(reader)
                width, height = info['size']
                self.assertEqual(height, 720)
                self.assertLessEqual(abs(width - 405), 2)
                self.assertEqual(sum(1 for _ in reader), 60)
            finally:
                reader.close()
            peaks = build_waveform(ffmpeg, source, folder, cancel)
            self.assertLess(max(peaks[:45]), .05)
            self.assertGreater(max(peaks[55:95]), .4)
            self.assertFalse((Path(folder) / 'waveform.pcm').exists())

    def test_portrait_canvas_leaves_background_on_both_sides(self):
        from PySide6.QtWidgets import QApplication
        from manual_edit_ui import PreviewCanvas
        app = QApplication.instance() or QApplication([])
        canvas = PreviewCanvas(1080, 1920, None)
        canvas.resize(900, 320)
        canvas.show()
        app.processEvents()
        rect = canvas.video.geometry()
        self.assertAlmostEqual(rect.width() / rect.height(), 1080 / 1920, delta=.01)
        self.assertGreater(rect.left(), 300)
        self.assertAlmostEqual(rect.center().x(), canvas.rect().center().x(), delta=1)
        canvas.close()
