"""Recorded-capture introspection shared by the preview transcode (ADR-0027) and
the pre-recorded session import (ADR-0031)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2


def frame_count(path: Path) -> int:
    """Number of frames in the recorded capture (0 if unreadable/unknown)."""
    capture = cv2.VideoCapture(str(path))
    try:
        return max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
    finally:
        capture.release()


@dataclass(frozen=True)
class VideoProperties:
    """Native geometry + cadence of a recorded/imported video.

    ``fps`` is the raw probed rate (may be ``0.0`` when the container omits it);
    ``frames`` is the reported count (``CAP_PROP_FRAME_COUNT``, an estimate for some
    codecs — same reliability the preview scrubber already relies on).
    """

    width: int
    height: int
    fps: float
    frames: int


def video_properties(path: Path) -> VideoProperties:
    """Probe a video file for its native size, frame rate and frame count.

    Mirrors :func:`frame_count` (a single ``cv2.VideoCapture`` open). Used by the
    import pipeline (ADR-0031) to derive a camera's config from an uploaded video —
    no live device involved. Raises ``ValueError`` if the file cannot be opened.
    """
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(f"cannot open video {path}")
        return VideoProperties(
            width=int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(capture.get(cv2.CAP_PROP_FPS)),
            frames=max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT))),
        )
    finally:
        capture.release()
