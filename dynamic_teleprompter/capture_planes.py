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

class PlaneCache:
    """Reuse the conversion of each unchanged image while other layers move."""

    def __init__(self):
        self.keys = [None, None, None]
        self.colors = [None, None, None]
        self.alpha = None
        self.neutral = None

    def planes(self, images, keys):
        width, height = images[0].width(), images[0].height()
        if width % 2 or height % 2:
            raise ValueError('Editable capture requires even canvas dimensions')
        if any(image.width() != width or image.height() != height for image in images):
            raise ValueError('Editable planes must have equal dimensions')
        size = width * height
        quarter = size // 4
        if self.neutral is None or len(self.neutral) != quarter:
            self.neutral = bytes([128]) * quarter
        for index, (image, key) in enumerate(zip(images, keys)):
            if key == self.keys[index]:
                continue
            native = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            bgra = np.frombuffer(native.constBits(), dtype=np.uint8).reshape(height, width, 4)
            converted = cv2.cvtColor(bgra, cv2.COLOR_BGRA2YUV_I420).reshape(-1)
            self.colors[index] = (memoryview(converted[:size]),
                                  memoryview(converted[size:size + quarter]),
                                  memoryview(converted[size + quarter:]))
            if index == 2:
                self.alpha = memoryview(np.ascontiguousarray(bgra[:, :, 3]).reshape(-1))
            self.keys[index] = key
        y, u, v = zip(*self.colors)
        return (*y, self.alpha, *u, self.neutral, *v, self.neutral)


def planes(images):
    return PlaneCache().planes(images, tuple(image.cacheKey() for image in images))
