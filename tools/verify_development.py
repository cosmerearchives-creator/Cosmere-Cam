"""Synthetic UI/render/processing tests; no devices, user media or Cubism assets."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dynamic_teleprompter'))
os.environ.update(QT_QPA_PLATFORM='offscreen', COSMERE_TEST_MODE='1')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import Qt
import app
from theme import apply_theme
from recording_processing import process_recording
from recorder import resolve_ffmpeg
import live2d_runtime as live


def main():
    qt = QApplication([])
    apply_theme(qt)
    results = {}
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        os.environ.update(USERPROFILE=str(temp), HOME=str(temp), LOCALAPPDATA=str(temp/'local'), APPDATA=str(temp/'roaming'))
        app.QSettings = lambda *args: QSettings(str(temp/'test.ini'), QSettings.Format.IniFormat)
        app.MainWindow.prompt_model_if_missing = lambda self: None
        app.MainWindow.arrange_windows = lambda self: None
        app.lip_microphone.start = lambda *args, **kwargs: None
        window = app.MainWindow()
        window.script.setPlainText('Test text')
        window.font_size.setValue(45)
        window.color_fg = '#ff0000'
        window.color_bg = '#000000'
        window.prepare_groups()
        window.next()
        layer = window.media_layers[0]
        window.render()
        before = window.out.render_frame(force=True).copy()
        editor = app.VisualLayerEditor(window)
        editor.items.setCurrentRow(0)
        editor.x.setValue(20)
        editor.y.setValue(-10)
        assert layer['x'] == 20 and layer['y'] == -10
        after = window.out.render_frame(force=True).copy()
        assert before != after
        editor.opacity.setValue(0)
        transparent = window.out.render_frame(force=True).copy()
        assert after != transparent
        editor.opacity.setValue(100)
        editor.drag_layer(5, 3)
        assert layer['x'] == 25 and layer['y'] == -7
        editor.locked.setChecked(True)
        editor.drag_layer(20, 20)
        assert layer['x'] == 25
        editor.locked.setChecked(False)
        assert not editor.loop.isEnabled()
        assert editor.text_font.isEnabled() and editor.sharpness.isEnabled()
        results['text_transform_opacity_drag_lock'] = True
        results['text_frame_differs_after_transform'] = True
        # Automatic recording waits for speech capture readiness and then focuses output.
        window.voice_worker = Mock(ready_for_recording=True)
        window.voice_worker.isRunning.return_value = True
        with patch.object(window, '_begin_recording') as begin:
            window._toggle_recording()
            begin.assert_called_once()
        window.voice_worker = None
        with patch('app.model_is_installed', return_value=True), patch.object(window, 'toggle_listening'):
            window._toggle_recording()
            assert window._record_start_pending
            with patch.object(window, '_begin_recording') as begin:
                window._voice_record_ready()
                begin.assert_called_once()
        window._record_start_pending = False
        window.record_toggle.setEnabled(True)
        results['recording_waits_for_voice_ready'] = True
        # A deliberately slow mesh renderer must not execute on the GUI thread.
        class SlowModel:
            last_pose = None
            parameter_defaults = []
            def set_tracking(self, pose):
                self.last_pose = pose
                return True
            def draw(self, painter, w, h):
                time.sleep(.13)
                painter.fillRect(0, 0, w, h, QColor('#2244aa'))
        live._core = Mock(load=lambda path: SlowModel())
        image = QImage(320, 240, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        start = time.perf_counter()
        painter = QPainter(image)
        live.draw_live2d(painter, dict(path='synthetic-model', type='live2d', visible=True), (320,240))
        painter.end()
        assert time.perf_counter()-start < .08
        time.sleep(.3)
        painter = QPainter(image)
        live.draw_live2d(painter, dict(path='synthetic-model',type='live2d'), (320,240))
        painter.end()
        assert live._pose_workers['synthetic-model'].updated
        results['slow_mesh_does_not_block_first_frame'] = True
        live.close_render_workers()
        # Real FFmpeg performs both trimming modes on generated tone/silence.
        ffmpeg = resolve_ffmpeg()
        import subprocess
        source = temp/'synthetic.mp4'
        subprocess.run([ffmpeg, '-y','-v','error','-f','lavfi','-i','color=c=blue:s=160x90:r=30:d=5',
            '-f','lavfi','-i',r"aevalsrc=if(between(t\,1\,2)+between(t\,3\,4)\,0.2*sin(2*PI*440*t)\,0):s=16000:d=5",
            '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(source)],check=True,capture_output=True)
        # A real Vosk model processes synthetic audio; every unmatched script word remains.
        model_path = os.environ.get('COSMERE_ALIGNMENT_TEST_MODEL')
        if not model_path:
            model_path = str(Path.home()/'.dynamic_teleprompter_models/vosk-model-small-en-us-0.15')
        if not (Path(model_path)/'am/final.mdl').is_file():
            import urllib.request, zipfile
            folder = ROOT/'build/alignment-test'
            folder.mkdir(parents=True,exist_ok=True)
            archive = folder/'model.zip'
            urllib.request.urlretrieve(app.MODEL_URL, archive)
            with zipfile.ZipFile(archive) as package:
                if sum(item.file_size for item in package.infolist()) > 200_000_000:
                    raise RuntimeError('Oversized alignment model')
                for item in package.infolist():
                    if not (folder/item.filename).resolve().is_relative_to(folder.resolve()):
                        raise RuntimeError('Unsafe model archive')
                package.extractall(folder)
            model_path = str(folder/'vosk-model-small-en-us-0.15')
        _, aligned = process_recording(source, ffmpeg, dict(align=True),
            'Every word must remain in this script.', model_path, 5, temp, lambda text: None)
        assert [word['word'] for word in aligned['alignment']['words']] == 'Every word must remain in this script.'.split()
        assert all(word['needs_approval'] for word in aligned['alignment']['words'])
        results['real_vosk_full_script_retained'] = True
        from processing_ui import AlignmentReview
        review_path = temp/'review.alignment.json'
        review = AlignmentReview(window, aligned['alignment'], review_path)
        review.show()
        qt.processEvents()
        review.next_section()
        assert {i.row() for i in review.table.selectedIndexes()} == set(range(len(aligned['alignment']['words'])))
        review.approve_selected()
        assert all(not word['needs_approval'] for word in aligned['alignment']['words'])
        assert review_path.is_file()
        review.close()
        results['estimated_section_approval_saved'] = True
        for mode in ('edges', 'pauses'):
            _, report = process_recording(source, ffmpeg, dict(trim=mode), '', '', 5, temp, lambda text: None)
            duration = sum(b-a for a,b in report['trim_segments'])
            assert 2 < duration < 4, (mode, duration)
            results[mode+'_trim_seconds'] = round(duration,3)
        # Two real decoders plus a slow synthetic Cubism pose at portrait resolution.
        import shutil
        second = temp/'second.mp4'
        shutil.copyfile(source, second)
        window.canvas_resolution.setCurrentText('1080x1920')
        window.media_layers = [dict(type='text',visible=True),
            dict(type='live2d',path='synthetic-model',visible=True),
            dict(type='video',path=str(source),visible=True,loop=True),
            dict(type='video',path=str(second),visible=True,loop=True)]
        window.render()
        app.VIDEO_FRAMES.shutdown()
        started = time.monotonic()
        rendered = window.out.render_count
        samples = []
        while time.monotonic()-started < 2.5:
            tick = time.perf_counter()
            window.out.render_frame()
            samples.append((time.perf_counter()-tick)*1000)
            qt.processEvents()
            time.sleep(.005)
        fps = (window.out.render_count-rendered)/(time.monotonic()-started)
        assert fps >= 20, fps
        assert len(app.VIDEO_FRAMES.decoders) == 2
        assert all(decoder.serial >= 30 for decoder in app.VIDEO_FRAMES.decoders.values())
        results['two_video_synthetic_model_canvas_fps'] = round(fps,2)
        results['canvas_call_mean_ms'] = round(sum(samples)/len(samples),2)
        results['live2d_fixture_is_synthetic'] = True
        live.close_render_workers()
        window.voice_worker = None
        window._saved_project_snapshot = window._project_snapshot()
        editor.close()
        window.close()
        qt.processEvents()
    target = ROOT/'dist/development-verification.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))

if __name__ == '__main__':
    main()
