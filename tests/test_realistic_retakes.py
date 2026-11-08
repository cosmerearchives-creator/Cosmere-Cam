"""Generic regressions for real-world restarts; no user scripts or media fixtures."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dynamic_teleprompter'))
from edit_pipeline import propose_retakes,normalize_word
from recording_processing import process_recording,align_script
from PySide6.QtGui import QImage,QPainter,QColor
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication


def heard(text,start=0,step=.25):
    return [dict(word=w,start=start+i*step,end=start+i*step+.2,conf=.99) for i,w in enumerate(text.split())]

class RealisticRetakes(unittest.TestCase):
    def test_long_attempt_beyond_twenty_words(self):
        script='The museum curator explained how the ancient collection traveled across several countries before it finally reached the new gallery where visitors could examine every object in the exhibition.'
        first=heard(script);second=heard(script,first[-1]['end']+.12)
        cuts=propose_retakes(script,first+second)
        self.assertEqual(len(cuts),1)
        self.assertAlmostEqual(cuts[0]['end'],second[0]['start']-.06)
        self.assertLess(cuts[0]['confidence'],.95)
    def test_multiple_restarts_and_internal_stutters_keep_final_attempt(self):
        script='The unusual markings on the old map suggest that the expedition traveled through the northern mountains before reaching the coast.'
        attempts=['The unusual markings on the old map suggest that the expedition traveled near',
                  'The unusual markings on the old map suggest that the expedition traveled through the the northern',
                  'The unusual markings on the old map suggest that the expedition traveled through the northern mountains before',script]
        all_words=[];start=0
        for phrase in attempts:
            part=heard(phrase,start);all_words+=part;start=part[-1]['end']+.12
        final_start=all_words[-len(script.split())]['start']
        cuts=propose_retakes(script,all_words)
        self.assertEqual(len(cuts),1,cuts)
        self.assertAlmostEqual(cuts[0]['end'],final_start-.06)
        self.assertFalse(cuts[0]['approved'])
    def test_unicode_and_asr_mistakes(self):
        script='And when you look closer, that’s actually a different pattern.'
        words=heard("and when you look closer that's actually the different picture")+heard("and when you look closer that's actually a different pattern",3)
        cuts=propose_retakes(script,words)
        self.assertEqual(len(cuts),1)
        self.assertEqual(normalize_word('that’s'),normalize_word("that's"))
        alignment=align_script('that’s',heard("that's"),1)
        self.assertFalse(alignment['words'][0]['needs_approval'])
    def test_badly_recognized_first_words_are_included_in_cut(self):
        script='But Mara spent many hours carefully restoring the damaged painting.'
        first=heard('but maras went many hours carefully restoring the damaged paint')
        second=heard('but mara spent many hours carefully restoring the damaged painting',3)
        cuts=propose_retakes(script,first+second)
        self.assertEqual(len(cuts),1)
        self.assertEqual(cuts[0]['start'],0)
        self.assertAlmostEqual(cuts[0]['end'],2.94)
    def test_intentional_repetition_is_not_removed(self):
        script='Read the label carefully. Read the label carefully.'
        self.assertFalse(propose_retakes(script,heard(script)))
        self.assertFalse(propose_retakes('Very very important words follow here.',heard('very very important words follow here')))
    def test_basic_trim_does_not_erase_detection_evidence(self):
        words=heard('we will open the garden',1)+heard('we will open the garden tomorrow',7)
        script='we will open the garden tomorrow'
        events=[]
        def recognize(*args):events.append('recognition');return words
        def run(command,**kwargs):
            events.append('silence' if 'silencedetect=noise=-40dB:d=0.35' in command else 'trim')
            return Mock(stderr=b'silence_start: 2.5\nsilence_end: 6.8\n')
        with tempfile.TemporaryDirectory() as folder,patch('recording_processing.recognize_words',side_effect=recognize),patch('recording_processing.run',side_effect=run):
            _,result=process_recording(Path(folder)/'source.mp4','ffmpeg',dict(align=True,advanced='assisted',trim='pauses',minimum_pause=1.2,source_fps=30),script,'model',10,folder,lambda stage:None)
        self.assertEqual(events[0],'recognition')
        data=result['alignment'];self.assertEqual(len(data['cuts']),1)
        self.assertEqual(data['retake_detection_timeline'],'before_basic_trimming')
        self.assertLess(data['cuts'][0]['end'],data['cuts'][0]['original_end'])
        self.assertEqual(len(data['words']),len(script.split()))
        self.assertLessEqual(data['words'][-1]['end'],data['duration'])

class RenderRegression(unittest.TestCase):
    def test_transparent_crop_keeps_model_pixel_positions(self):
        import numpy as np
        from live2d_runtime import visible_bounds
        qt=QApplication.instance() or QApplication([])
        source=QImage(64,96,QImage.Format.Format_ARGB32_Premultiplied);source.fill(QColor(0,0,0,0))
        p=QPainter(source);p.fillRect(20,40,20,30,QColor(220,40,10,180));p.end()
        x,y,w,h=visible_bounds(source)
        self.assertLess(w,source.width());self.assertLess(h,source.height())
        reference=QImage(192,288,source.format());reference.fill(QColor(0,0,0,0));cropped=reference.copy()
        for image,crop in [(reference,False),(cropped,True)]:
            painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if crop:painter.drawImage(QRectF(x*3,y*3,w*3,h*3),source,QRectF(x,y,w,h))
            else:painter.drawImage(QRectF(0,0,192,288),source)
            painter.end()
        a=np.frombuffer(reference.constBits(),dtype=np.uint8).astype(int)
        b=np.frombuffer(cropped.constBits(),dtype=np.uint8).astype(int)
        self.assertLessEqual(abs(a-b).max(),2)
        empty=QImage(32,32,source.format());empty.fill(QColor(0,0,0,0));self.assertEqual(visible_bounds(empty),(0,0,0,0))

    def test_cached_glow_preserves_fade_compositing(self):
        import numpy as np
        import app
        from PySide6.QtGui import QPainterPath,QFont
        qt=QApplication.instance() or QApplication([])
        label=app.StyledOutputLabel();label.visual=dict(glow=True,glow_width=8,glow_color='#0502a7',foreground='#eeeeff',outline_width=2,outline_color='#ffffff',shadow=True,shadow_x=3,shadow_y=2)
        path=QPainterPath();path.addText(30,90,QFont('Arial',28),'Glowing words')
        for opacity in (.3,1.):
            a=QImage(500,160,QImage.Format.Format_ARGB32_Premultiplied);a.fill(QColor(0,0,0,0));b=a.copy()
            for image,method in [(a,label._paint_style_path),(b,label._draw_styled)]:
                p=QPainter(image);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setOpacity(opacity);method(p,path);p.end()
            x=np.frombuffer(a.constBits(),dtype=np.uint8).astype(int);y=np.frombuffer(b.constBits(),dtype=np.uint8).astype(int)
            self.assertLessEqual(abs(x-y).max(),2)
