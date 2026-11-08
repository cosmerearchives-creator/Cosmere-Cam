"""Portable project, speech aliases and post-processing regressions (synthetic only)."""
import os,sys,json,tempfile,threading,time,unittest,zipfile,subprocess
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dynamic_teleprompter'))
from project_package import plan_project,export_project,load_exported_project,relative_file
from pronunciations import validate,rewrite_words,rewrite_tokens,dictionary_word
from recording_processing import align_script
from update_core import version_key

class PortableProjects(unittest.TestCase):
    def project(self,root):
        media=root/'clip.mp4';media.write_bytes(b'synthetic video fixture')
        model=root/'model';model.mkdir();(model/'mesh.moc3').write_bytes(b'synthetic model fixture, not Cubism data')
        (model/'texture.png').write_bytes(b'synthetic texture fixture')
        manifest=model/'test.model3.json';manifest.write_text(json.dumps(dict(FileReferences=dict(Moc='mesh.moc3',Textures=['texture.png']))))
        data=dict(format='DynamicTeleprompterProject',version=1,script='A test script',settings=dict(media_layers=[dict(type='video',path=str(media)),dict(type='live2d',path=str(manifest),movement=dict(direction_models=dict(left=str(manifest))))],audio_chain=[dict(type='vst2',path='C:/plugins/example.dll',enabled=True)]),model_library=[dict(name='Fixture',path=str(manifest))])
        return data
    def test_export_extract_move_and_reconnect_every_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=self.project(root);plan=plan_project(data);target=root/'project.zip'
            export_project(plan,target)
            extracted=root/'moved';extracted.mkdir()
            with zipfile.ZipFile(target) as archive:archive.extractall(extracted)
            loaded,warnings=load_exported_project(extracted)
            self.assertFalse(warnings);self.assertEqual(loaded['script'],data['script'])
            for layer in loaded['settings']['media_layers']:self.assertTrue(Path(layer['path']).is_file());self.assertTrue(Path(layer['path']).resolve().is_relative_to(extracted.resolve()))
            model=Path(loaded['model_library'][0]['path']);self.assertTrue((model.parent/'texture.png').is_file())
            self.assertEqual(loaded['settings']['media_layers'][1]['movement']['direction_models']['left'],str(model))
            self.assertFalse(loaded['settings']['audio_chain'][0]['enabled']);self.assertEqual(loaded['settings']['audio_chain'][0]['path'],'example.dll')
            self.assertEqual(len(plan['files']),4)
    def test_cancel_does_not_replace_existing_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);plan=plan_project(self.project(root));target=root/'existing.zip';target.write_bytes(b'previous export')
            event=threading.Event();event.set()
            with self.assertRaisesRegex(RuntimeError,'cancelled'):export_project(plan,target,cancel=event)
            self.assertEqual(target.read_bytes(),b'previous export');self.assertFalse(list(root.glob('*.partial')))
    def test_missing_asset_and_escaping_model_reference_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);data=self.project(root);Path(data['settings']['media_layers'][0]['path']).unlink()
            with self.assertRaisesRegex(ValueError,'Missing'):plan_project(data)
            data['settings']['media_layers'].pop(0)
            model=Path(data['model_library'][0]['path']);model.write_text(json.dumps(dict(FileReferences=dict(Moc='../secret.moc3'))))
            with self.assertRaisesRegex(ValueError,'Unsafe'):plan_project(data)
    def test_package_paths_reject_absolute_unc_and_parent_escape(self):
        for path in ('../outside','/outside','C:/outside','\\server\\share','assets/../../outside'):
            with self.assertRaises(ValueError):relative_file(Path.cwd(),path)
    def test_changed_media_is_reported_and_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);plan=plan_project(self.project(root));target=root/'project.zip';export_project(plan,target)
            out=root/'out';out.mkdir()
            with zipfile.ZipFile(target) as archive:archive.extractall(out)
            asset=next(name for name in plan['files'] if name.endswith('.mp4'));(out/asset).write_bytes(b'modified')
            _,warnings=load_exported_project(out);self.assertEqual(len(warnings),1)

class PronunciationMatching(unittest.TestCase):
    def aliases(self):return [dict(name='Veloria',alternatives=['the lorry a','val ore ya'])]
    def test_multiword_heard_alias_advances_one_script_name(self):
        from voice_matching import match_progress
        script=['the','city','of','veloria','returns']
        self.assertEqual(match_progress(script,['city','of','the','lorry','a','returns'],0,aliases=self.aliases()),5)
    def test_saved_name_alignment_retains_original_spelling_and_audio_span(self):
        recognized=[dict(word=w,start=i*.2,end=(i+1)*.2,conf=.99) for i,w in enumerate(['the','lorry','a'])]
        mapped=rewrite_words(recognized,self.aliases(),'Veloria')
        data=align_script('Veloria',mapped,1)
        self.assertEqual(data['words'][0]['word'],'Veloria');self.assertEqual(data['words'][0]['status'],'Matched saved pronunciation')
        self.assertAlmostEqual(data['words'][0]['end'],.6);self.assertFalse(data['words'][0]['needs_approval'])
    def test_alias_cannot_overwrite_an_intentional_script_phrase(self):
        words=['the','lorry','a'];script='The lorry a driver used went to Veloria.'
        self.assertEqual(rewrite_tokens(words,self.aliases(),script),words)
    def test_conflicting_name_aliases_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'two names'):validate(self.aliases()+[dict(name='Othername',alternatives=['the lorry a'])])
    def test_straight_and_curly_apostrophes_share_spelling_lookup(self):
        from spellchecker import SpellChecker
        speller=SpellChecker()
        for word in ('doesn’t','wouldn’t','there’s','didn’t','that’s','it’s'):
            self.assertFalse(speller.unknown([dictionary_word(word)]))
    def test_stable_release_updates_existing_alpha_launchers(self):
        self.assertGreater(version_key('0.6.0'),version_key('0.5.0-alpha26'))
        self.assertGreater(version_key('0.6.0'),version_key('0.6.0-alpha99'))

class Vst3HelperBoundary(unittest.TestCase):
    def test_independent_backend_self_test_does_not_import_pedalboard_in_parent(self):
        import vst3_isolation
        if not vst3_isolation.available():self.skipTest('Optional VST3 backend not installed locally')
        before='pedalboard' in sys.modules;host=vst3_isolation.Host()
        try:self.assertTrue(host.request(('self_test',),8)['gain_ok'])
        finally:host.close()
        self.assertEqual('pedalboard' in sys.modules,before)

class UiPlanningFeatures(unittest.TestCase):
    def test_elapsed_time_keeps_advancing_during_speech_analysis_without_percent(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from PySide6.QtWidgets import QApplication
        from processing_session import ProcessingSession
        from processing_ui import RecordingProgress
        qt=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            session=ProcessingSession(Path(folder)/'processing');session.update(50,speed='0.2x')
            session.begin('Matching recorded speech')
            self.assertEqual(session.state['speed'],'')
            recorder=SimpleNamespace(path=Path(folder)/'take.mp4',processing_progress=dict(session.state),processing_session=session,finished=False)
            dialog=RecordingProgress(None,recorder)
            with patch('processing_ui.time.monotonic',return_value=session.started+9):dialog.refresh()
            self.assertIn('Elapsed: 9s',dialog.details.text());self.assertEqual(dialog.bar.maximum(),0)
            dialog.timer.stop();dialog.close();qt.processEvents()

    def test_owned_windows_and_contractions(self):
        from PySide6.QtWidgets import QApplication,QWidget,QTextEdit
        from app import Proofreader
        from window_order import set_owner
        qt=QApplication.instance() or QApplication([])
        parent=QWidget();child=QWidget();set_owner(parent,child,True)
        self.assertEqual(child.windowHandle().transientParent(),parent.windowHandle())
        set_owner(parent,child,False);self.assertIsNone(child.windowHandle().transientParent())
        editor=QTextEdit();proof=Proofreader(editor.document())
        for word in ('doesn’t','wouldn’t','there’s','didn’t','that’s','it’s'):self.assertFalse(proof.misspelled(word))
        self.assertTrue(proof.misspelled('zznonexistentwordzz'))
        editor.close();child.close();parent.close();qt.processEvents()
    def test_name_listener_finishes_before_other_microphone_work(self):
        from unittest.mock import Mock,patch
        from app import MainWindow
        window=Mock();window.pronunciation_panel.worker.isRunning.return_value=True
        window.pronunciation_panel.worker.wait.return_value=True
        self.assertTrue(MainWindow._stop_name_listener(window));window.pronunciation_panel.stop.assert_called_once()
        window.pronunciation_panel.worker.wait.return_value=False
        with patch('app.QMessageBox.information') as warning:
            self.assertFalse(MainWindow._stop_name_listener(window));warning.assert_called_once()

    def test_disabled_vst2_effect_is_not_prepared_at_startup(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication,QWidget
        from plugin_ui import PluginController
        qt=QApplication.instance() or QApplication([])
        class Window(QWidget):
            def _audio_chain_snapshot(self):return [dict(type='vst2',enabled=False,path='reastream.dll')]
        window=Window();controller=PluginController(window)
        with patch('vst2_host.has_hosts',return_value=False),patch('vst2_host.prepare') as prepare:
            controller.prepare_chain();prepare.assert_not_called();self.assertEqual(controller.preparing,0)
        controller.timer.stop();window.close();qt.processEvents()

class RealProcessing(unittest.TestCase):
    def test_many_cuts_keep_audio_video_duration_and_diagnostics(self):
        import imageio_ffmpeg
        from edit_pipeline import cut_media,keep_filter
        from processing_session import ProcessingSession,active
        from recorder import resolve_ffmpeg
        ffmpeg=resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mkv';target=root/'trim.mp4'
            subprocess.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=20:duration=4','-f','lavfi','-i','sine=frequency=1000:sample_rate=48000:duration=4','-c:v','libx264','-preset','ultrafast','-c:a','pcm_s16le',str(source)],capture_output=True,check=True,timeout=20)
            segments=[(i*.1,i*.1+.05) for i in range(40)]
            graph=keep_filter(segments);self.assertEqual(graph.count('[0:v]'),1);self.assertIn('select=',graph)
            session=ProcessingSession(root/'diagnostics');session.begin('Trimming recording');session.duration=2
            with active(session):cut_media(source,target,segments,ffmpeg,root)
            session.finish();reader=imageio_ffmpeg.read_frames(str(target),pix_fmt='rgb24');metadata=next(reader);frames=list(reader);reader.close()
            self.assertEqual(len(frames),40);self.assertAlmostEqual(metadata['duration'],2,delta=.06)
            audio=subprocess.run([ffmpeg,'-v','error','-i',str(target),'-vn','-ac','1','-ar','48000','-f','s16le','pipe:1'],capture_output=True,check=True,timeout=15).stdout
            self.assertAlmostEqual(len(audio)/96000,2,delta=.06)
            report=json.loads(session.path.read_text());self.assertGreater(report['active_processing_seconds'],0);self.assertEqual(report['commands'][0]['returncode'],0)
            self.assertTrue(list(session.directory.glob('*filters.ffscript')));self.assertIn('Input #',next(session.directory.glob('*ffmpeg.log')).read_text())
    def test_review_wait_is_separate_and_export_timings_update_main_diagnostics(self):
        from processing_session import ProcessingSession
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);diagnostics=root/'take.diagnostics.json';diagnostics.write_text(json.dumps(dict(capture_calls=42)))
            analysis=ProcessingSession(root/'processing');analysis.report['diagnostics_path']=str(diagnostics)
            analysis.begin('Matching recorded speech');time.sleep(.04);analysis.finish('review')
            active_before=analysis.report['active_processing_seconds'];time.sleep(.02)
            export=ProcessingSession(root/'processing');export.begin_export();export.begin('Exporting baseline');time.sleep(.04);export.finish('complete')
            report=json.loads(diagnostics.read_text())
            self.assertEqual(report['capture_calls'],42);self.assertGreater(report['post_processing']['review_wait_seconds'],.01)
            self.assertGreater(report['post_processing']['active_processing_seconds'],active_before)
            self.assertIn('Exporting baseline',[item['stage'] for item in report['post_processing']['stages']])

    def test_cancelled_ffmpeg_retains_log_and_original(self):
        from processing_session import ProcessingSession
        from recorder import resolve_ffmpeg
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);original=root/'original.mp4';original.write_bytes(b'original take')
            session=ProcessingSession(root/'diagnostics');session.begin('Cancellation test');cancel=threading.Event();timer=threading.Timer(.3,cancel.set);timer.start()
            try:
                with self.assertRaisesRegex(RuntimeError,'cancelled'):session.run([resolve_ffmpeg(),'-v','error','-f','lavfi','-i','anullsrc','-f','null','-'],cancel=cancel)
            finally:timer.join()
            self.assertEqual(original.read_bytes(),b'original take');self.assertTrue(list(session.directory.glob('*ffmpeg.log')))

if __name__=='__main__':unittest.main()
