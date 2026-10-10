"""A single captured frame with its CLOCK_MONOTONIC capture timestamp."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class Frame:
    """One captured image plus the metadata needed downstream.

    ``timestamp`` is on CLOCK_MONOTONIC, the clock of ``time.monotonic``: the
    V4L2 buffer stamp of the frame, or the host clock at its grab when the
    driver's is unusable (one base per open, ADR-0049). It is the *only* basis
    for cross-camera synchronization (ADR-0007).
    """

    timestamp: float  # CLOCK_MONOTONIC seconds (ADR-0007, ADR-0049)
    image: NDArray[np.uint8]  # BGR, at capture resolution
