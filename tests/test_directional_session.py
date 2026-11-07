import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys, tempfile, unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dynamic_teleprompter'))
from PySide6.QtCore import QSettings, Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QLineEdit, QComboBox, QSpinBox
import app
from live2d_movement import select_direction_model
from voice_matching import match_progress

class DirectionModels(unittest.TestCase):
    def test_diagonal_and_release(self):
        layer=dict(path='original',x=12,y=3,scale=.7,movement=dict(model_mode='models',
                   direction_models=dict(a='left',d='right',default='rest'),release_model='default'))
        select_direction_model(layer,{'a','d'},['a','d'])
        self.assertEqual(layer['_movement_model_path'],'right')
        select_direction_model(layer,{'a'},['a'])
        self.assertEqual(layer['_movement_model_path'],'left')
        select_direction_model(layer,set(),[])
        self.assertEqual(layer['_movement_model_path'],'rest')
        layer['movement']['release_model']='last'
        select_direction_model(layer,set(),[])
        self.assertEqual(layer['_movement_model_path'],'left')
        self.assertEqual((layer['path'],layer['x'],layer['y'],layer['scale']),('original',12,3,.7))
    def test_unassigned_and_head_only(self):
        layer=dict(path='original',movement=dict(model_mode='both'))
        select_direction_model(layer,{'w'},['w'])
        self.assertEqual(layer['_movement_model_path'],'original')
        layer['movement']['model_mode']='head'
        self.assertTrue(select_direction_model(layer,set(),[]))
        self.assertNotIn('_movement_model_path',layer)

class VoiceMatching(unittest.TestCase):
    def test_contractions(self):
        self.assertEqual(match_progress(['i',"don't",'know'],['i','do','not','know'],0),3)
    def test_name_recovery(self):
        self.assertEqual(match_progress(['welcome','to','adolin','kholin'],['welcome','to','adolin','kholinn'],0),4)
    def test_no_hallucinated_jump(self):
        self.assertEqual(match_progress(['one','two','three','four','five','six','seven','eight'],['eight'],0),0)
        self.assertEqual(match_progress(['one','two','three'],['unrelated','noise'],0),0)
    def test_repeated_and_skipped_words(self):
        words='we walk across the bridge and then turn left'.split()
        self.assertEqual(match_progress(words,'we walk across bridge'.split(),0),5)
        self.assertEqual(match_progress(words,'and then turn left'.split(),5),9)
        self.assertEqual(match_progress(words,'we walk'.split(),5),5)

class SuiteInputSession(unittest.TestCase):
    def setUp(self):
        self.qt=QApplication.instance() or QApplication([])
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch('app.SETTINGS_PATH',root/'missing.json'))
        self.stack.enter_context(patch('app.QSettings',side_effect=lambda *args: QSettings(str(root/'prefs.ini'),QSettings.Format.IniFormat)))
        self.stack.enter_context(patch.object(app.MainWindow,'prompt_model_if_missing'))
        self.stack.enter_context(patch.object(app.MainWindow,'arrange_windows'))
        self.stack.enter_context(patch.object(app.lip_microphone,'start'))
        self.stack.enter_context(patch('app.draw_live2d'))
        self.window=app.MainWindow()
        self.window.media_layers=[dict(type='live2d',path='test',movement=dict(enabled=True)),dict(type='text')]
        self.window.render()
        self.addCleanup(self.close_window)
    def close_window(self):
        self.window._saved_project_snapshot=self.window._project_snapshot()
        self.window.close();self.qt.processEvents()
    def event(self,key,kind=QEvent.Type.KeyPress,repeat=False):
        return QKeyEvent(kind,key,Qt.KeyboardModifier.NoModifier,'',repeat)
    def test_arrows_in_any_dialog_and_no_button_navigation(self):
        dialog=QDialog(self.window); button=QPushButton('Button',dialog)
        with patch.object(QApplication,'activeWindow',return_value=dialog),patch.object(QApplication,'focusWidget',return_value=button):
            self.assertTrue(self.window.eventFilter(button,self.event(Qt.Key.Key_Left)))
            self.assertEqual(self.window._movement_keys,{'a'})
            self.assertTrue(self.window.eventFilter(button,self.event(Qt.Key.Key_Left,repeat=True)))
            self.assertEqual(self.window._movement_order,['a'])
            self.assertTrue(self.window.eventFilter(button,self.event(Qt.Key.Key_Left,QEvent.Type.KeyRelease)))
            self.assertFalse(self.window._movement_keys)
        dialog.deleteLater()
    def test_text_exception_and_focus_release(self):
        editor=QLineEdit()
        with patch.object(QApplication,'activeWindow',return_value=self.window),patch.object(QApplication,'focusWidget',return_value=editor):
            self.assertFalse(self.window.eventFilter(editor,self.event(Qt.Key.Key_Left)))
            self.assertFalse(self.window._movement_keys)
            self.window._movement_keys.add('a')
            self.window.eventFilter(editor,QEvent(QEvent.Type.FocusIn))
            self.assertFalse(self.window._movement_keys)
    def test_noneditable_combo_and_spin_arrows_consumed(self):
        for widget in [QComboBox(),QSpinBox()]:
            with patch.object(QApplication,'activeWindow',return_value=self.window),patch.object(QApplication,'focusWidget',return_value=widget):
                self.assertTrue(self.window.eventFilter(widget,self.event(Qt.Key.Key_Up)))
        self.window.eventFilter(self.window,QEvent(QEvent.Type.ApplicationDeactivate))
        self.assertFalse(self.window._movement_keys)
    def test_checkpoint_restores_workspace_without_capture(self):
        self.window.script.setPlainText('Restore this full script please')
        self.window.prepare_groups();self.window.next()
        self.window.media_layers[0].update(x=17,_movement_last_model='right')
        self.window.main_tabs.setCurrentIndex(2)
        self.window._save_session()
        saved=self.window.local_settings.value('previous_session_v1','',type=str)
        self.assertNotIn('_movement_turn',saved)
        self.window.script.clear();self.window.media_layers=[]
        self.window._restore_session()
        self.assertEqual(self.window.script.toPlainText(),'Restore this full script please')
        self.assertEqual(self.window.media_layers[0]['x'],17)
        self.assertEqual(self.window.media_layers[0]['_movement_last_model'],'right')
        self.assertEqual(self.window.main_tabs.currentIndex(),2)
        self.assertFalse(self.window.awaiting_first)
        self.assertIsNone(self.window.recorder)
        self.assertIsNone(self.window.voice_worker)
    def test_new_window_restores_checkpoint(self):
        self.window.script.setPlainText('A session restored on startup')
        self.window.prepare_groups()
        self.window._save_session()
        other=app.MainWindow()
        try:
            self.assertEqual(other.script.toPlainText(),'A session restored on startup')
            self.assertIsNone(other.recorder)
            self.assertIsNone(other.voice_worker)
        finally:
            other._saved_project_snapshot=other._project_snapshot()
            other.close()
    def test_corrupt_checkpoint(self):
        self.window.local_settings.setValue('previous_session_v1','{invalid')
        self.window._restore_session()
        self.assertFalse(self.window.local_settings.contains('previous_session_v1'))

if __name__=='__main__': unittest.main()

class DirectionalRenderer(unittest.TestCase):
    def test_render_path_head_mode_and_missing_model_fallback(self):
        from PySide6.QtGui import QImage,QPainter,QColor
        import live2d_runtime as runtime
        qt=QApplication.instance() or QApplication([])
        class Worker:
            def __init__(self,color):
                self.image=QImage(32,32,QImage.Format.Format_ARGB32_Premultiplied)
                self.image.fill(QColor(color));self.requests=[]
            def request(self,layer,size):
                self.requests.append(layer)
                return self.image,0,0
        original,right,rest=Worker('blue'),Worker('red'),Worker('green')
        workers={'original':original,'right':right,'rest':rest}
        layer=dict(path='original',_movement_model_path='right',_movement_turn=1,
                   movement=dict(enabled=True,model_mode='models',direction_models=dict(d='right',default='rest')))
        target=QImage(32,32,QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(runtime,'_pose_workers',workers),patch.object(runtime,'live2d_error',return_value=''):
            painter=QPainter(target);runtime.draw_live2d(painter,layer,(32,32));painter.end()
            self.assertEqual(right.requests[-1]['path'],'right')
            self.assertNotIn('ParamAngleY',right.requests[-1]['_pose_override'])
            layer['movement']['model_mode']='both'
            painter=QPainter(target);runtime.draw_live2d(painter,layer,(32,32));painter.end()
            self.assertGreater(right.requests[-1]['_pose_override']['ParamAngleY'],0)
            layer['_movement_model_path']='rest'
            with patch.object(runtime,'live2d_error',side_effect=lambda path: 'missing' if path=='rest' else ''):
                painter=QPainter(target);runtime.draw_live2d(painter,layer,(32,32));painter.end()
                self.assertEqual(target.pixelColor(16,16).name(),'#ff0000')
