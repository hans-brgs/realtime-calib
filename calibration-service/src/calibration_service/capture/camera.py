"""Single-camera capture wrapper around a ``VideoSource``.

Reads frames defensively (a USB camera can vanish mid-capture, ADR-0007 /
service CLAUDE.md): a failed or raising read yields ``None`` rather than
crashing the loop.

Frames are stamped with the V4L2 buffer timestamp (ADR-0049): uvcvideo stamps
each frame on CLOCK_MONOTONIC, the clock of ``time.monotonic()``, when its first
USB packet arrives, whatever the service is doing meanwhile. A host stamp taken
after decoding lagged it by 24 ms idle and 48 ms under load, varying by 6 to 9 ms.
The base is chosen ONCE per open (``choose_clock``): one capture never mixes the
kernel stamps with host ones, 24 to 48 ms apart.
"""

from __future__ import annotations

import logging
import math
import subprocess
import time
from types import TracebackType
from typing import Literal, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.capture.source import VideoSource
from calibration_service.models.frame import Frame

logger = logging.getLogger(__name__)

# V4L2 controls applied BEFORE opening (samvision pattern): auto-exposure on, and
# lock the fps. exposure_dynamic_framerate=0 is required even though firmware
# defaults to 0 — the device initialises to 1, which sacrifices fps for exposure
# in low light and corrupts frames on slower (USB 2.0) links.
_V4L2_EXPOSURE_CTRLS = ("auto_exposure=3", "exposure_dynamic_framerate=0")
# Small capture buffer: avoids stale/corrupt accumulated frames without underrun.
_CAPTURE_BUFFER_SIZE = 2
# Timestamp base decision (ADR-0049): this many frames, each with a kernel stamp
# finite, > 0, at most _AHEAD_S in the future and at most _BEHIND_S in the past.
_CLOCK_SAMPLES = 3
_CLOCK_ATTEMPTS = 5  # each failed grab may last a whole V4L2 select() timeout
_CLOCK_AHEAD_S = 0.005
_CLOCK_BEHIND_S = 1.0

Clock = Literal["v4l2", "host"]


def _configure_v4l2_exposure(device_node: str) -> None:
    """Apply the V4L2 exposure controls before opening the device (best-effort)."""
    for ctrl in _V4L2_EXPOSURE_CTRLS:
        try:
            subprocess.run(
                ["v4l2-ctl", "--device", device_node, f"--set-ctrl={ctrl}"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.warning("v4l2-ctl --set-ctrl=%s failed for %s", ctrl, device_node)


class CameraError(Exception):
    """Base class for camera-related failures."""


class CameraOpenError(CameraError):
    """Raised when a camera device cannot be opened."""


class CameraCapture:
    """Reads timestamped frames from a single video source."""

    def __init__(self, source: VideoSource, camera_index: int) -> None:
        self._source = source
        self._camera_index = camera_index
        # Timestamp base, chosen once by choose_clock(); host until then.
        self._clock: Clock = "host"
        self._last_kernel_s: float | None = None
        self._grab_stamp: float | None = None  # timestamp of the last grabbed frame
        self._dropped = 0  # frames whose kernel stamp did not increase

    @property
    def clock(self) -> Clock:
        """The timestamp base of this capture: "v4l2" (kernel) or "host"."""
        return self._clock

    @property
    def dropped(self) -> int:
        """Frames dropped because their kernel timestamp did not increase."""
        return self._dropped

    def choose_clock(self) -> Clock:
        """Pick the timestamp base ONCE, on a few frames grabbed for that purpose.

        "v4l2" when every sample's kernel stamp is plausible against now (finite,
        > 0, not in the future, under a second old): the driver stamps on our
        clock. Otherwise "host", ``time.monotonic()`` at grab, for the whole
        capture: a time namespace, another uvcvideo ``clock`` or a driver without
        stamps lands here. The sample frames are neither recorded nor detected.
        """
        stamps: list[tuple[float | None, float]] = []
        for _ in range(_CLOCK_ATTEMPTS):
            if len(stamps) == _CLOCK_SAMPLES:
                break
            if not self._grab_source():
                # A camera that stops right after its first frame would cost a full
                # select() timeout per attempt: decide now, the loop's loss rule
                # handles a camera that really stopped.
                break
            stamps.append((self._kernel_seconds(), time.monotonic()))
        valid = len(stamps) == _CLOCK_SAMPLES and all(
            kernel is not None and now - _CLOCK_BEHIND_S <= kernel <= now + _CLOCK_AHEAD_S
            for kernel, now in stamps
        )
        self._clock = "v4l2" if valid else "host"
        if valid:
            self._last_kernel_s = stamps[-1][0]
            lag_ms = 1000 * (stamps[-1][1] - (stamps[-1][0] or 0.0))
            logger.info(
                "camera %d: kernel timestamps (V4L2 buffer), %.1f ms before the host clock",
                self._camera_index,
                lag_ms,
            )
        else:
            logger.info(
                "camera %d: host timestamps (no usable kernel stamp: %s)",
                self._camera_index,
                stamps,
            )
        return self._clock

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
        # A read is a first-frame probe, before the loop: host stamp, never recorded.
        return self._frame(image, time.monotonic())

    def grab(self) -> float | None:
        """Buffer the next frame **without decoding** it (cheap); its timestamp, or None.

        Paired with :meth:`retrieve`, this lets the capture loop drain frames it is
        about to drop (pacing below the native rate) without paying the JPEG decode.
        The timestamp is taken HERE, at grab (ADR-0049), and ``retrieve`` carries it.

        None: no frame, or (kernel base) a frame whose stamp did not increase,
        which is dropped (neither written nor detected) and counted. A frame
        that waited in the driver's queue keeps its own, older, kernel stamp.
        """
        if not self._grab_source():
            return None
        if self._clock == "host":
            self._grab_stamp = time.monotonic()
            return self._grab_stamp
        kernel = self._kernel_seconds()
        if kernel is None or (self._last_kernel_s is not None and kernel <= self._last_kernel_s):
            self._dropped += 1
            self._grab_stamp = None  # nothing for retrieve() to stamp
            if self._dropped == 1:
                logger.warning(
                    "camera %d: a kernel timestamp did not increase (%s after %s); frame dropped",
                    self._camera_index,
                    kernel,
                    self._last_kernel_s,
                )
            return None
        self._last_kernel_s = kernel
        self._grab_stamp = kernel
        return kernel

    def retrieve(self) -> Frame | None:
        """Decode the most recently grabbed frame into a ``Frame`` stamped at its grab."""
        try:
            ok, image = self._source.retrieve()
        except Exception:
            logger.exception("camera %d: retrieve() raised", self._camera_index)
            return None
        if not ok or image is None:
            return None
        stamp = self._grab_stamp if self._grab_stamp is not None else time.monotonic()
        return self._frame(image, stamp)

    def _grab_source(self) -> bool:
        try:
            return bool(self._source.grab())
        except Exception:
            logger.exception("camera %d: grab() raised", self._camera_index)
            return False

    def _kernel_seconds(self) -> float | None:
        """The V4L2 buffer timestamp of the last grab, in seconds; None if unusable.

        ``CAP_PROP_POS_MSEC`` is the ``VIDIOC_DQBUF`` timestamp OpenCV kept at
        grab, 0 before the first capture (cap_v4l.cpp, OpenCV 4.13).
        """
        try:
            milliseconds = float(self._source.get(cv2.CAP_PROP_POS_MSEC))
        except Exception:
            return None
        if not math.isfinite(milliseconds) or milliseconds <= 0.0:
            return None
        return milliseconds / 1000.0

    def _frame(self, image: NDArray[np.uint8], timestamp: float) -> Frame:
        return Frame(timestamp=timestamp, image=image)

    def release(self) -> None:
        if self._dropped:
            logger.info(
                "camera %d: %d frame(s) dropped for a non-increasing kernel timestamp",
                self._camera_index,
                self._dropped,
            )
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
    # Exposure/fps controls must be applied before opening (samvision pattern).
    _configure_v4l2_exposure(device_node)

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
    source.set(cv2.CAP_PROP_BUFFERSIZE, _CAPTURE_BUFFER_SIZE)

    # cv2.VideoCapture satisfies VideoSource at runtime; its overloaded, stubbed
    # read() signature does not match structurally, so cast at this boundary.
    return CameraCapture(cast(VideoSource, source), camera_index)
