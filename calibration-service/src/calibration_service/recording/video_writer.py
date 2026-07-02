"""Record the raw capture to disk during a calibration sweep (ADR-0019, [[calibration-recording]]).

A calibration is made reproducible by keeping the source video next to the config
and results, so it can be replayed/recomputed offline ([[replay-recalibration]]).
Format: FFV1 (lossless) in an ``.mkv`` container — ChArUco detection is sensitive to
compression, and mkv tolerates truncation if capture stops abruptly.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.session.store import session_dir

_FOURCC = "FFV1"  # lossless; quality knob (e.g. H.264 CRF) is a later Settings option
_INTRINSIC_DIR = "intrinsic"
_CAPTURE_FILE = "capture.mkv"


class RecordingError(RuntimeError):
    """Raised when the video writer cannot be opened."""


def intrinsic_capture_path(sessions_dir: Path, session_id: str, camera_name: str) -> Path:
    """``<sessions_dir>/<session_id>/intrinsic/<camera_name>/capture.mkv``."""
    return session_dir(sessions_dir, session_id) / _INTRINSIC_DIR / camera_name / _CAPTURE_FILE


class VideoRecorder:
    """Writes BGR frames to a lossless mkv. One recorder per camera capture."""

    def __init__(self, path: Path, width: int, height: int, fps: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter.fourcc(*_FOURCC), float(fps), (width, height)
        )
        if not self._writer.isOpened():
            raise RecordingError(f"cannot open video writer for {path}")
        self._frames = 0

    def write(self, image: NDArray[np.uint8]) -> None:
        """Append one BGR frame (must match the writer's width/height)."""
        self._writer.write(image)
        self._frames += 1

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        self._writer.release()

    def __enter__(self) -> VideoRecorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
