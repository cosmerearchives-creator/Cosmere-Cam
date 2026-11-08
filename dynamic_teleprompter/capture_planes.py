"""Compact planar transport for three editable colors and an exact alpha mask.

YUV420 color is the same format used by the capture encoder. The fourth tile's
luma contains unscaled alpha bytes; its chroma is neutral and never encoded.
Conversion and transport run exclusively on the recording writer thread.
"""
import cv2
import numpy as np
from PySide6.QtGui import QImage

# Small independent frames are faster without OpenCV's machine-wide thread pool.
cv2.setNumThreads(1)

def planes(images):
    native=[image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied) for image in images]
    width,height=native[0].width(),native[0].height()
    if width%2 or height%2:raise ValueError('Editable capture requires even canvas dimensions')
    if any(image.width()!=width or image.height()!=height for image in native):raise ValueError('Editable planes must have equal dimensions')
    bgra=[np.frombuffer(image.constBits(),dtype=np.uint8).reshape(height,width,4) for image in native]
    converted=[cv2.cvtColor(image,cv2.COLOR_BGRA2YUV_I420).reshape(-1) for image in bgra]
    size=width*height;quarter=size//4
    alpha=np.ascontiguousarray(bgra[2][:,:,3]).reshape(-1)
    neutral=bytes([128])*quarter
    return [memoryview(image[:size]) for image in converted]+[memoryview(alpha)]+[memoryview(image[size:size+quarter]) for image in converted]+[neutral]+[memoryview(image[size+quarter:]) for image in converted]+[neutral]
