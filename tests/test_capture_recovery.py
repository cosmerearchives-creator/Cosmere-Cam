"""Regression coverage for compact editable capture and bounded plugin transport."""
import os,sys,threading,queue,time,tempfile,wave,unittest
from pathlib import Path
from unittest.mock import patch,Mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dynamic_teleprompter'))
from PySide6.QtGui import QImage,QColor
from recorder import CanvasRecorder
from vst2_host import Host

class PluginDeadlines(unittest.TestCase):
    def host(self):
        host=Host.__new__(Host)
        host.lock=threading.RLock();host.replies=queue.Queue(maxsize=4);host.requests=queue.Queue(maxsize=1)
        host.process=Mock();host.process.poll.return_value=None
        return host
    def test_blocked_pipe_write_cannot_trap_audio_request(self):
        host=self.host();release=threading.Event()
        host.process.stdin.write.side_effect=lambda payload:release.wait(3)
        def kill():host.process.poll.return_value=1;release.set()
        host.process.kill.side_effect=kill
        writer=threading.Thread(target=host._write,daemon=True);writer.start()
        start=time.monotonic()
        with self.assertRaisesRegex(RuntimeError,'timed out'):host.request(1,b'pcm',timeout=.05)
        self.assertLess(time.monotonic()-start,.5)
        host.process.kill.assert_called_once();writer.join(1);self.assertFalse(writer.is_alive())
    def test_contended_request_lock_is_bounded(self):
        host=self.host();ready=threading.Event();release=threading.Event()
        def hold():
            with host.lock:ready.set();release.wait(3)
        worker=threading.Thread(target=hold);worker.start();ready.wait(1)
        try:
            start=time.monotonic()
            with self.assertRaisesRegex(RuntimeError,'timed out'):host.request(1,b'pcm',timeout=.05)
            self.assertLess(time.monotonic()-start,.5)
            host.process.kill.assert_called_once()
        finally:release.set();worker.join(1)

class Recovery(unittest.TestCase):
    def test_preparation_failure_retains_closed_audio_and_video(self):
        with tempfile.TemporaryDirectory() as folder:
            r=CanvasRecorder(Path(folder)/'take.mp4',64,64,10,microphone=False,editable=True);r.start()
            with wave.open(str(r._wave),'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(48000);wav.writeframes(bytes(9600))
            image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor('blue'))
            r.push_frame(image,image,image)
            with patch('edit_pipeline.run_media',side_effect=RuntimeError('preparation failed')):
                r.finish_async();deadline=time.monotonic()+15
                while not r.finished and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(r.finished);self.assertIn('preparation failed',r.error)
            self.assertTrue(r.path.with_suffix('.recovery.mkv').is_file())
            with wave.open(str(r.path.with_suffix('.recovery.wav')),'rb') as wav:self.assertEqual(wav.getnframes(),4800)
            self.assertFalse(r.path.exists())
    def test_compact_capture_preserves_exact_alpha_mask(self):
        import numpy as np,subprocess
        from recorder import resolve_ffmpeg
        with tempfile.TemporaryDirectory() as folder:
            r=CanvasRecorder(Path(folder)/'mask.mp4',64,64,10,microphone=False,editable=True);r.start()
            image=QImage(64,64,QImage.Format.Format_ARGB32_Premultiplied);image.fill(QColor(0,0,0,0))
            pixels=np.frombuffer(image.bits(),dtype=np.uint8).reshape(64,64,4)
            expected=np.tile(np.arange(64,dtype=np.uint8)*4,(64,1));pixels[:,:,3]=expected
            r.push_frame(image,image,image)
            with patch('edit_pipeline.run_media',side_effect=RuntimeError('retain packed fixture')):
                r.finish_async();deadline=time.monotonic()+15
                while not r.finished and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(r.finished)
            result=subprocess.run([resolve_ffmpeg(),'-v','error','-i',str(r.path.with_suffix('.recovery.mkv')),'-map','0:v:1','-frames:v','1','-f','rawvideo','-pix_fmt','gray','pipe:1'],capture_output=True,check=True,timeout=15)
            self.assertEqual(result.stdout,expected.tobytes())

    def test_pause_rejects_capture_without_waiting_for_device_close(self):
        r=CanvasRecorder('unused.mp4',64,64,10);r._started=time.monotonic();r._stream=Mock()
        r.pause_capture();self.assertTrue(r.stopping);r._stream.stop.assert_not_called()

if __name__=='__main__':unittest.main()
