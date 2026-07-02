"""Read individual frames from a recorded capture, for the *Prepare* scrubber.

ADR-0022 (Option C): the intrinsic sweep is recorded as MJPG-in-mkv — already a
sequence of independent JPEG frames — so the webapp scrubs it by asking for frame
``n`` on demand, no transcoding. MJPG is all-intra, so seeking is frame-accurate.

Reads are stateless (open → seek → read → close per call): simple and correct. If
fast scrubbing shows latency, cache an open ``VideoCapture`` per camera later.
"""

from __future__ import annotations

from pathlib import Path

import cv2

_JPEG_QUALITY = 90  # preview scrub; re-encode of an already-JPEG frame, visually lossless


def frame_count(path: Path) -> int:
    """Number of frames in the recorded capture (0 if unreadable/unknown)."""
    capture = cv2.VideoCapture(str(path))
    try:
        return max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
    finally:
        capture.release()


def read_frame_jpeg(path: Path, index: int) -> bytes | None:
    """Return frame ``index`` as JPEG bytes, or ``None`` if out of range/unreadable."""
    if index < 0:
        return None
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok or frame is None:
            return None
        encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
        return buffer.tobytes() if encoded else None
    finally:
        capture.release()
