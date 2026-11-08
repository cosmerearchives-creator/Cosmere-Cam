"""Processing must use the tested runtime, independent of external encoder settings."""
import os,sys,tempfile,subprocess,time,unittest,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dynamic_teleprompter'))
import recorder

class ProcessingRuntime(unittest.TestCase):
    def test_bundled_binary_wins_over_configured_path_and_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binary=root/'binaries'/'ffmpeg-fixture.exe';binary.parent.mkdir();binary.write_bytes(b'fixture')
            binary.chmod(0o755)
            with patch.dict(sys.modules,{'imageio_ffmpeg':SimpleNamespace(__file__=str(root/'__init__.py'))}),patch.dict(os.environ,{'IMAGEIO_FFMPEG_EXE':'external-development.exe'}),patch('recorder.resolve_ffmpeg',side_effect=AssertionError('external runtime selected')):
                self.assertEqual(recorder.resolve_processing_ffmpeg('custom.exe'),str(binary.resolve()))
    def test_source_install_without_bundle_keeps_existing_fallback(self):
        with patch('recorder.bundled_ffmpeg',return_value=None),patch('recorder.resolve_ffmpeg',return_value='custom.exe') as resolve:
            self.assertEqual(recorder.resolve_processing_ffmpeg('custom.exe'),'custom.exe');resolve.assert_called_once_with('custom.exe')
    def test_recording_hands_finalization_to_bundled_encoder(self):
        from PySide6.QtGui import QImage,QColor
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);r=recorder.CanvasRecorder(root/'capture.mp4',64,64,10,microphone=False)
            r.start();capture=r._ffmpeg
            image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('blue'));r.push_frame(image)
            # Exercise the actual finish worker, not just resolver discovery.
            with patch('recorder.resolve_processing_ffmpeg',wraps=recorder.resolve_processing_ffmpeg) as choose:
                r.finish_async();deadline=time.monotonic()+15
                while not r.finished and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(r.finished);self.assertIsNone(r.error);choose.assert_called_once_with(capture)
            report=json.loads(r.report_path.read_text());self.assertEqual(report['capture_ffmpeg'],capture)
            self.assertEqual(report['processing_ffmpeg'],recorder.bundled_ffmpeg())
            self.assertTrue(report['post_processing']['ffmpeg_selection']['bundled'])

class LongTrim(unittest.TestCase):
    def test_fractional_start_and_end_keep_exact_frame_count(self):
        from edit_pipeline import cut_media
        import imageio_ffmpeg
        ffmpeg=recorder.resolve_processing_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'frames.mp4';target=root/'trim.mp4'
            subprocess.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','testsrc2=size=64x64:rate=30:duration=1','-c:v','libx264','-preset','ultrafast',str(source)],capture_output=True,check=True,timeout=10)
            cut_media(source,target,[(2/30,10/30)],ffmpeg,root,audio=False)
            reader=imageio_ffmpeg.read_frames(str(target),pix_fmt='rgb24');next(reader);count=sum(1 for _ in reader);reader.close()
            self.assertEqual(count,8)

    def test_three_minute_rgb_aac_take_with_widely_spaced_cuts(self):
        from edit_pipeline import cut_media
        from processing_session import ProcessingSession,active
        import imageio_ffmpeg
        ffmpeg=recorder.resolve_processing_ffmpeg('external-development-build.exe')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'rgb.mp4';target=root/'trim.mp4'
            # Long input timelines reproduce scheduler/buffering issues that a
            # four-second many-cut fixture cannot cover. No user media or timings.
            subprocess.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','testsrc2=size=160x120:rate=30:duration=204',
                '-f','lavfi','-i','sine=frequency=700:sample_rate=48000:duration=204','-c:v','libx264rgb','-preset','ultrafast','-crf','0','-threads:v','1','-c:a','aac',str(source)],capture_output=True,check=True,timeout=30)
            frame_spans=[(61,1961),(2071,2134),(2252,3511),(3631,3722),(3842,3931),(3991,4082),(4202,5071),(5221,5312),(5402,5941),(6061,6120)]
            segments=[(a/30,b/30) for a,b in frame_spans]
            expected=sum(b-a for a,b in segments)
            session=ProcessingSession(root/'processing');session.begin('Long recording trimming');session.duration=expected
            # A regression is a test failure; never let CI wait the production
            # 180-second no-progress allowance to diagnose a deterministic hang.
            original_run=session.run
            with patch.object(session,'run',side_effect=lambda command,**kwargs:original_run(command,stall_timeout=8,**kwargs)):
                with active(session):cut_media(source,target,segments,ffmpeg,root)
            session.finish()
            reader=imageio_ffmpeg.read_frames(str(target),pix_fmt='rgb24');metadata=next(reader);frames=sum(1 for _ in reader);reader.close()
            self.assertEqual(frames,round(expected*30));self.assertAlmostEqual(metadata['duration'],expected,delta=.04)
            audio=subprocess.run([ffmpeg,'-v','error','-i',str(target),'-vn','-ac','1','-ar','48000','-f','s16le','pipe:1'],capture_output=True,check=True,timeout=15).stdout
            self.assertAlmostEqual(len(audio)/96000,expected,delta=.04)
            log=next(session.directory.glob('*ffmpeg.log')).read_text(encoding='utf-8');self.assertIn('yuv420p',log)

if __name__=='__main__':unittest.main()
