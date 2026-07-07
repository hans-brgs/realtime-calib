"""Recorded-capture introspection shared by the preview transcode (ADR-0027)."""

from __future__ import annotations

from pathlib import Path

import cv2


def frame_count(path: Path) -> int:
    """Number of frames in the recorded capture (0 if unreadable/unknown)."""
    capture = cv2.VideoCapture(str(path))
    try:
        return max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
    finally:
        capture.release()
