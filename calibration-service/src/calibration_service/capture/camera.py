"""Single-camera capture wrapper around a ``VideoSource``.

Reads frames defensively (a USB camera can vanish mid-capture, ADR-0007 /
service CLAUDE.md): a failed or raising read yields ``None`` rather than
crashing the loop. Stamps each frame with a host-monotonic timestamp.
"""

from __future__ import annotations

import logging
import time
from types import TracebackType
from typing import cast

import cv2

from calibration_service.capture.source import VideoSource
from calibration_service.models.frame import Frame

logger = logging.getLogger(__name__)


class CameraError(Exception):
    """Base class for camera-related failures."""


class CameraOpenError(CameraError):
    """Raised when a camera device cannot be opened."""


class CameraCapture:
    """Reads timestamped frames from a single video source."""

    def __init__(self, source: VideoSource, camera_index: int) -> None:
        self._source = source
        self._camera_index = camera_index
        self._frame_id = 0

    @property
    def camera_index(self) -> int:
        return self._camera_index

    def read(self) -> Frame | None:
        """Read the next frame, or ``None`` if the read failed.

        Returning ``None`` (instead of raising) lets the capture loop degrade
        gracefully: drop the frame, log, keep going.
        """
        try:
            ok, image = self._source.read()
        except Exception:
            logger.exception("camera %d: read() raised", self._camera_index)
            return None
        if not ok or image is None:
            return None

        timestamp = time.monotonic()
        self._frame_id += 1
        return Frame(
            camera_index=self._camera_index,
            frame_id=self._frame_id,
            timestamp=timestamp,
            image=image,
        )

    def release(self) -> None:
        try:
            self._source.release()
        except Exception:
            logger.exception("camera %d: release() raised", self._camera_index)

    def __enter__(self) -> CameraCapture:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def open_camera(
    device_node: str,
    camera_index: int,
    *,
    fourcc: str | None = "MJPG",
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
) -> CameraCapture:
    """Open a V4L2 camera by device node (e.g. ``/dev/video0``).

    Defaults to the MJPG pixel format: it is compressed (~10x less USB
    bandwidth than raw YUYV), which lets several USB cameras stream at once
    without bandwidth starvation (incomplete frames render as green). Pass
    ``fourcc=None`` to keep the driver default (e.g. raw YUYV when single-camera
    precision matters). Resolution/fps are hints; the driver may pick the
    nearest supported mode.
    """
    source = cv2.VideoCapture(device_node, cv2.CAP_V4L2)
    if not source.isOpened():
        source.release()
        raise CameraOpenError(f"cannot open camera device {device_node!r}")

    # FOURCC must be set before resolution for V4L2 to pick the right mode.
    if fourcc is not None:
        source.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*fourcc))
    if width is not None:
        source.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    if height is not None:
        source.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps is not None:
        source.set(cv2.CAP_PROP_FPS, fps)

    # cv2.VideoCapture satisfies VideoSource at runtime; its overloaded, stubbed
    # read() signature does not match structurally, so cast at this boundary.
    return CameraCapture(cast(VideoSource, source), camera_index)
