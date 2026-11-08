"""Native Windows ownership/z-order regression; no user projects or hardware."""
import os,sys,json,ctypes,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'dynamic_teleprompter'))
os.environ['QT_QPA_PLATFORM']='windows'
from PySide6.QtWidgets import QApplication,QWidget
from window_order import set_owner

def main():
    qt=QApplication([]);main=QWidget();out=QWidget();reader=QWidget();other=QWidget()
    windows=(main,out,reader,other)
    for index,window in enumerate(windows):window.setWindowTitle('Suite ownership fixture '+str(index));window.resize(220,150);window.show()
    for child in (out,reader):set_owner(main,child,True)
    qt.processEvents()
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindow.argtypes=[ctypes.c_void_p,ctypes.c_uint];user.GetWindow.restype=ctypes.c_void_p
    user.GetWindowLongPtrW.argtypes=[ctypes.c_void_p,ctypes.c_int];user.GetWindowLongPtrW.restype=ctypes.c_ssize_t
    for child in (out,reader):
        assert user.GetWindow(int(child.winId()),4)==int(main.winId())
        assert not user.GetWindowLongPtrW(int(child.winId()),-20)&8,'Display is globally topmost'
    main.raise_();main.activateWindow();qt.processEvents()
    def above(child,owner):
        handle=user.GetWindow(int(owner.winId()),3)
        for _ in range(5000):
            if not handle:return False
            if handle==int(child.winId()):return True
            handle=user.GetWindow(handle,3)
        raise AssertionError('Window order traversal did not end')
    for child in (out,reader):assert above(child,main),'Main window covered owned display'
    other.raise_();other.activateWindow();qt.processEvents()
    for child in (out,reader):assert above(other,child),'Display stayed above unrelated window'
    for child in (out,reader):set_owner(main,child,False);assert not user.GetWindow(int(child.winId()),4)
    result=dict(owned_above_control_center=True,no_global_topmost=True,unrelated_window_can_cover=True,toggle_releases_ownership=True)
    for window in reversed(windows):window.close()
    qt.processEvents();(ROOT/'dist').mkdir(exist_ok=True)
    (ROOT/'dist/windows-owner-verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
if __name__=='__main__':main()
