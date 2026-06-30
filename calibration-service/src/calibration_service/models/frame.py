"""A single captured frame with its host-monotonic timestamp."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class Frame:
    """One captured image plus the metadata needed downstream.

    ``timestamp`` is a host-monotonic value (``time.monotonic``) taken at read
    time; it is the *only* basis for cross-camera synchronization (ADR-0007),
    never ``frame_id`` (which is per-camera and not comparable across cameras).
    """

    camera_index: int
    frame_id: int
    timestamp: float  # host-monotonic seconds (ADR-0007)
    image: NDArray[np.uint8]  # BGR, at capture resolution
