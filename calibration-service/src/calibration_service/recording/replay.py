"""Recorded-capture introspection shared by the preview transcode (ADR-0027) and
the pre-recorded session import (ADR-0035)."""

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


def declared_fps(path: Path) -> float:
    """Container-declared frame rate (0.0 if unreadable/unknown).

    Since ADR-0037 the recorded mkv declares the true capture cadence, so this
    IS the rate the preview transcode re-times at (dynamic scrubber contract).
    """
    capture = cv2.VideoCapture(str(path))
    try:
        return float(capture.get(cv2.CAP_PROP_FPS))
    finally:
        capture.release()


def decoded_frame_count(path: Path) -> int:
    """EXACT frame count by decoding the whole file (no metadata trust).

    ``CAP_PROP_FRAME_COUNT`` is an estimate on some containers (an imported
    remux read 2258 where 2257 frames decode) — off-by-a-few is fatal for the
    Caliscope-parity alignment, whose grids must match the frames the compute
    will actually decode. Costs a full sequential decode; import-time only.
    """
    capture = cv2.VideoCapture(str(path))
    count = 0
    try:
        while True:
            ok = capture.grab()  # decode-free advance: ~3x faster than read()
            if not ok:
                return count
            count += 1
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
    import pipeline (ADR-0035) to derive a camera's config from an uploaded video —
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
