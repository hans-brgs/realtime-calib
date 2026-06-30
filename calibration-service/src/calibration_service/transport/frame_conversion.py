"""Convert OpenCV BGR frames to LiveKit ``rtc.VideoFrame`` (RGBA).

Split into a pure colour conversion (unit-testable without LiveKit) and the
``VideoFrame`` wrapping, so the colour-channel ordering can be tested directly.
"""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
from livekit import rtc
from numpy.typing import NDArray


def bgr_to_rgba(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Convert a BGR image (OpenCV default) to RGBA."""
    return cast("NDArray[np.uint8]", cv2.cvtColor(image, cv2.COLOR_BGR2RGBA))


def rgba_to_video_frame(rgba: NDArray[np.uint8]) -> rtc.VideoFrame:
    """Wrap a contiguous RGBA image as a LiveKit ``VideoFrame``."""
    height, width = rgba.shape[:2]
    return rtc.VideoFrame(width, height, rtc.VideoBufferType.RGBA, rgba.tobytes())


def bgr_to_video_frame(image: NDArray[np.uint8]) -> rtc.VideoFrame:
    """Convert a BGR image straight to a LiveKit RGBA ``VideoFrame``."""
    return rgba_to_video_frame(bgr_to_rgba(image))
