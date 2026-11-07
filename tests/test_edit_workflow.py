import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy,json,sys,tempfile,time,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dynamic_teleprompter'))
from PySide6.QtGui import QImage,QColor,QPainter
from PySide6.QtWidgets import QApplication
from edit_pipeline import (propose_retakes,complement,remap_alignment,caption_events,validate_alignment,export_videos)
from recording_processing import align_script
import test_directional_session as fixtures


def speech(text,start=0,step=.3):
    return [dict(word=word,start=start+i*step,end=start+i*step+.2,conf=.99) for i,word in enumerate(text.split())]

class EditDecisions(unittest.TestCase):
    def test_script_unique_repeat_and_later_complete_take(self):
        words=speech('we are going to the archive',0)+speech('we are going to the archive today',3)
        cuts=propose_retakes('we are going to the archive today',words)
        self.assertEqual(len(cuts),1)
        self.assertGreaterEqual(cuts[0]['confidence'],.95)
        self.assertAlmostEqual(cuts[0]['end'],2.94)
        self.assertFalse(cuts[0]['approved'])
    def test_intentional_script_repeat_and_single_words_not_cut(self):
        self.assertFalse(propose_retakes('very very important',speech('very very important')))
        text='we are here we are here'
        self.assertFalse(propose_retakes(text,speech('we are here',0)+speech('we are here',3)))
    def test_stumbled_prefix_requires_review(self):
        cuts=propose_retakes('we are going to the archive',speech('we are',0)+speech('we are going to the archive',2))
        self.assertEqual(len(cuts),1);self.assertLess(cuts[0]['confidence'],.95)
    def test_merging_cuts_and_time_remapping(self):
        cuts=[dict(start=1,end=3,approved=True),dict(start=2,end=4,approved=True),dict(start=6,end=7,approved=False)]
        kept=complement(10,cuts);self.assertEqual(kept,[(0,1),(4,10)])
        data=align_script('one two three',speech('one two three',4),10)
        result=remap_alignment(data,kept)
        self.assertEqual(result['duration'],7)
        self.assertEqual(result['words'][0]['start'],1)
        self.assertEqual(data['duration'],10)
    def test_validation_rejects_unapproved_nonfinite(self):
        data=align_script('one',[],1)
        with self.assertRaises(ValueError):validate_alignment(data,True)
        data['words'][0]['start']=float('nan')
        with self.assertRaises(ValueError):validate_alignment(data)
    def test_all_words_reveal_even_with_zero_duration_estimates(self):
        data=align_script('one two three',[],1)
        data['words'][0].update(start=0,end=0)
        data['words'][1].update(start=0,end=.5)
        events=caption_events(data,['one two three'])
        self.assertEqual(events[0][1],'one two')
        self.assertEqual(events[-1][1],'one two three')

class WorkflowUi(unittest.TestCase):
    setUp=fixtures.SuiteInputSession.setUp
    close_window=fixtures.SuiteInputSession.close_window
    def test_start_recording_first_slide_preserves_and_unmutes_voice(self):
        import app
        worker=Mock(ready_for_recording=True);worker.isRunning.return_value=True
        self.window.voice_worker=worker;self.window.voice_enabled=True;self.window.voice_muted=True
        self.window.start_first_slide.setChecked(True);self.window.tracking_mode.setCurrentText('Manual')
        recorder=Mock(error=None);recorder.frames_due.return_value=0
        try:
            with patch('app.CanvasRecorder',return_value=recorder),patch.object(self.window,'stop_listening',wraps=self.window.stop_listening) as stop:
                self.window._toggle_recording()
                stop.assert_not_called();worker.stop.assert_not_called();worker.set_muted.assert_called_with(False)
                self.assertTrue(self.window.voice_enabled);self.assertFalse(self.window.voice_muted)
                self.assertEqual(self.window.tracking_mode.currentText(),'Automatic')
                self.assertTrue(self.window.quick_listen_button.isChecked())
                worker.reset_recognition.assert_called_once()
        finally:self.window.recorder=None;self.window.voice_worker=None;self.window.record_timer.stop()
    def test_start_waits_for_stopping_voice_worker(self):
        worker=Mock(running=False);worker.isRunning.return_value=True
        self.window.voice_worker=worker
        with patch.object(self.window,'_begin_recording') as begin:
            self.window._toggle_recording()
            begin.assert_not_called()
            self.assertTrue(self.window._record_start_pending)
        with patch.object(self.window,'_toggle_recording') as retry:
            self.window.on_voice_finished();self.qt.processEvents();retry.assert_called_once()
        self.assertIsNone(self.window.voice_worker)
    def test_capture_clock_and_export_block_restart(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog
        from update_ui import critical_operation
        out=self.window.out
        out.render_frame(force=True);count=out.render_count
        out.capture_owner=object();out._dirty=True
        out.render_frame();self.assertEqual(out.render_count,count)
        out.render_frame(force=True);self.assertEqual(out.render_count,count+1)
        out.capture_owner=None
        dialog=QDialog(self.window);dialog.timer=QTimer(dialog);dialog.timer.start(100)
        self.assertTrue(critical_operation(self.window))
        dialog.timer.stop();dialog.close()
    def test_first_slide_control_fits_text(self):
        self.window.show();self.qt.processEvents()
        box=self.window.quick_first_slide
        self.assertGreaterEqual(box.width(),box.sizeHint().width())
    def test_review_save_feedback_and_unapproved_export_block(self):
        import processing_ui
        data=align_script('full script',[],2)
        data['source']=dict(scene='not-present.mp4',width=320,height=240,fps=10)
        review=processing_ui.AlignmentReview(self.window,data,self.window.local_settings.fileName()+'.alignment.json')
        self.assertTrue(review.save());self.assertIn('Review saved',review.status.text())
        with patch('processing_ui.QMessageBox.information') as message:
            review.start_export();message.assert_called_once()
        review.approve_all();self.assertTrue(all(not word['needs_approval'] for word in data['words']))
        review.close()

class RealEditableExport(unittest.TestCase):
    def test_source_bundle_then_approved_baseline_and_advanced_exports(self):
        from recorder import CanvasRecorder,resolve_ffmpeg
        from export_ui import ExportDialog
        import imageio_ffmpeg
        import numpy as np
        qt=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'take.mp4'
            recorder=CanvasRecorder(path,320,240,10,microphone=False,editable=True)
            recorder.render_project=dict(groups=['Hello world'],settings=dict(font='Arial',font_size=18,foreground='#FFFFFF',
                canvas_resolution='320x240',chars=30,lines=2,media_layers=[dict(type='text',visible=True)]))
            recorder.start()
            original=QImage(320,240,QImage.Format.Format_ARGB32_Premultiplied);original.fill(QColor('red'))
            background=QImage(original.size(),original.format());background.fill(QColor('blue'))
            foreground=QImage(original.size(),original.format());foreground.fill(QColor(0,0,0,0))
            painter=QPainter(foreground);painter.fillRect(0,0,30,30,QColor('#00ff00'));painter.fillRect(40,0,30,30,QColor(255,0,0,128));painter.end()
            frame=[0];recorder.elapsed=lambda:frame[0]/10
            for i in range(20):
                frame[0]=i;recorder.push_frame(original,background,foreground);time.sleep(.015)
            self.assertIsNone(recorder.error)
            alignment=align_script('Hello world',speech('Hello',.5)+speech('world',1.1),2)
            alignment['advanced_mode']='assisted';alignment['cuts']=[dict(start=1.5,end=1.9,reason='test pause',confidence=1,approved=True)]
            recorder.processing_options=dict(align=True)
            with patch('recording_processing.process_recording',side_effect=lambda source,*args:(source,dict(alignment=copy.deepcopy(alignment),trim_segments=[(0.,2.)]))):
                recorder.finish_async()
                deadline=time.monotonic()+30
                while not recorder.finished and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(recorder.finished);self.assertIsNone(recorder.error)
            self.assertFalse(path.exists(),'Final export must wait for approval')
            review_path=path.with_suffix('.alignment.json');data=json.loads(review_path.read_text())
            self.assertTrue(Path(data['source']['scene']).is_file())
            self.assertTrue(Path(data['source']['original']).is_file())
            dialog=ExportDialog(None,data,review_path);dialog.show()
            deadline=time.monotonic()+30
            while 'exports' not in data and time.monotonic()<deadline:
                qt.processEvents();time.sleep(.005)
                if dialog.worker is not None and not dialog.worker.isRunning() and 'Export stopped' in dialog.status.text():break
            self.assertIn('exports',data,dialog.status.text())
            while dialog.worker.isRunning():qt.processEvents();time.sleep(.01)
            qt.processEvents()
            def decode(file):
                frames=imageio_ffmpeg.read_frames(str(file),pix_fmt='rgb24');metadata=next(frames)
                pixels=[np.frombuffer(payload,dtype=np.uint8).reshape(240,320,3) for payload in frames];frames.close()
                return pixels
            baseline=decode(data['exports']['baseline']);edited=decode(data['exports']['edited'])
            self.assertGreater(len(baseline),len(edited))
            self.assertTrue(np.all(baseline[2][100,100]<[10,10,260])) # no red baked-in text/frame
            self.assertGreater(int(baseline[7][10,10,1]),200) # foreground alpha retained
            self.assertTrue(90<int(baseline[7][10,50,0])<165,baseline[7][10,50].tolist())
            self.assertTrue(90<int(baseline[7][10,50,2])<165)
            white=lambda image:int(((image[:,:,0]>170)&(image[:,:,1]>170)&(image[:,:,2]>170)).sum())
            self.assertLess(white(baseline[2]),20)
            self.assertGreater(white(baseline[8]),100)
            self.assertGreater(white(baseline[14]),white(baseline[8]))
            map_path=Path(data['exports']['edited']).with_suffix('.edits.json')
            self.assertTrue(map_path.is_file())
            self.assertTrue(Path(data['source']['original']).is_file())
            dialog.close();qt.processEvents()
    def test_auxiliary_failure_keeps_playable_capture(self):
        from recorder import CanvasRecorder
        with tempfile.TemporaryDirectory() as folder:
            recorder=CanvasRecorder(Path(folder)/'fallback.mp4',64,64,10,microphone=False,editable=True)
            recorder.start()
            frame=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);frame.fill(QColor('blue'))
            recorder.push_frame(frame,frame,frame)
            recorder.companions[0].error='simulated auxiliary failure'
            recorder.finish_async();deadline=time.monotonic()+20
            while not recorder.finished and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(recorder.finished)
            self.assertIsNone(recorder.error)
            self.assertTrue(recorder.path.is_file())
            self.assertIn('Editable source',recorder.processing_error)
    def test_discard_editable_take_skips_processing_and_removes_tracks(self):
        from recorder import CanvasRecorder
        with tempfile.TemporaryDirectory() as folder:
            recorder=CanvasRecorder(Path(folder)/'discard.mp4',64,64,10,microphone=False,editable=True)
            recorder.start();temp=Path(recorder._temp.name)
            with patch('recording_processing.process_recording') as process:
                recorder.abort();process.assert_not_called()
            self.assertFalse(temp.exists());self.assertFalse(recorder.path.exists())

class AudioAndCancellation(unittest.TestCase):
    def test_real_audio_stays_with_video_and_cancel_preserves_existing(self):
        from edit_pipeline import run_media,write_concat
        from recorder import resolve_ffmpeg
        import wave
        ffmpeg=resolve_ffmpeg()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source.mp4'
            run_media([ffmpeg,'-y','-v','error','-f','lavfi','-i','color=blue:s=64x64:r=10:d=2',
                '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-c:v','libx264','-c:a','aac',str(source)])
            image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor(0,0,0,0))
            image.save(str(root/'000000.png'));concat=root/'captions.ffconcat'
            write_concat([(0,'',0)],[root/'000000.png'],2,concat)
            data=align_script('one two',speech('one',.2)+speech('two',1.5),2)
            data.update(source=dict(scene=str(source),width=64,height=64,fps=10,audio=True),advanced_mode='assisted',
                cuts=[dict(start=.85,end=1.24,approved=True,reason='pause',confidence=1)])
            result=export_videos(data,concat,root/'take',ffmpeg,root,lambda stage:None,threading.Event())
            pcm=root/'edited.wav';run_media([ffmpeg,'-y','-v','error','-i',result['edited'],'-vn','-ac','1','-c:a','pcm_s16le',str(pcm)])
            with wave.open(str(pcm)) as stream:duration=stream.getnframes()/stream.getframerate()
            report=json.loads(Path(result['edited']).with_suffix('.alignment.json').read_text())
            self.assertAlmostEqual(report['duration'],1.6)
            self.assertAlmostEqual(duration,report['duration'],delta=.05)
            self.assertNotIn('source',report)
            self.assertAlmostEqual(report['words'][1]['start'],1.1)
            before=Path(result['baseline']).read_bytes()
            cancel=threading.Event();cancel.set()
            with self.assertRaises(RuntimeError):export_videos(data,concat,root/'take',ffmpeg,root,lambda stage:None,cancel)
            self.assertEqual(Path(result['baseline']).read_bytes(),before)
            self.assertTrue(source.is_file())
