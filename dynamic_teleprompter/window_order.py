"""Owned display windows stay above their Control Center, not above other apps."""
import os
from PySide6.QtGui import QGuiApplication

def set_owner(main,child,enabled=True):
    main.winId();child.winId()
    child.windowHandle().setTransientParent(main.windowHandle() if enabled else None)
    if os.name=='nt' and QGuiApplication.platformName()=='windows':
        import ctypes
        from ctypes import wintypes
        user=ctypes.WinDLL('user32',use_last_error=True)
        change=user.SetWindowLongPtrW;change.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t];change.restype=ctypes.c_ssize_t
        ctypes.set_last_error(0)
        result=change(int(child.winId()),-8,int(main.winId()) if enabled else 0)
        if not result and ctypes.get_last_error():raise ctypes.WinError(ctypes.get_last_error())
