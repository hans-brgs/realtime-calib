"""The minimal video-source interface the capture layer depends on.

Modelled on the subset of ``cv2.VideoCapture`` we use. Depending on this
Protocol (rather than ``cv2`` directly) lets ``CameraCapture`` be unit-tested
with a fake source, no hardware required.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class VideoSource(Protocol):
    """Structural interface matching the ``cv2.VideoCapture`` methods we call."""

    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]: ...

    def get(self, prop_id: int) -> float: ...

    def release(self) -> None: ...
