"""USB camera enumeration via V4L2 device nodes.

Two levels are provided:
- ``enumerate_cameras``: minimal probe (open + read one frame) — used by the
  Phase 0 publish path.
- ``enumerate_camera_devices``: full mode enumeration (all formats x resolutions
  x fps) by parsing ``v4l2-ctl --list-formats-ext`` — the camera-detection-config
  feature (Phase 1).

Device identity favours the stable ``/dev/v4l/by-path`` symlink over
``/dev/videoN`` (Camera entity invariant).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2

from calibration_service.models.camera import CameraDevice, CameraMode, Resolution

logger = logging.getLogger(__name__)

BY_PATH_DIR = Path("/dev/v4l/by-path")

# Parsing of `v4l2-ctl --device <node> --list-formats-ext`.
_FORMAT_RE = re.compile(r"\[\d+\]:\s*'(\w+)'")
_SIZE_RE = re.compile(r"Size:\s*Discrete\s+(\d+)x(\d+)")
_FPS_RE = re.compile(r"\(([\d.]+)\s*fps\)")
_V4L2_TIMEOUT_S = 5.0

# A USB camera exposes interface index0 (capture) and index1 (metadata); we
# only want the capture interface.
_CAPTURE_INTERFACE_SUFFIX = "-video-index0"


@dataclass(frozen=True)
class DetectedCamera:
    """A camera found during enumeration, before operator configuration."""

    index: int  # provisional ordering index (not the logical/anchor index yet)
    device_path: str  # stable identity (by-path symlink when available)
    device_node: str  # resolved /dev/videoN, used to open the device
    width: int
    height: int
    fps: float


def _candidate_paths() -> list[tuple[str, str]]:
    """Return sorted ``(stable_path, resolved_node)`` capture-device candidates.

    Deduplicates the several by-path symlinks (usb/usbv2/usbv3) that point at
    the same node. Falls back to ``/dev/video*`` when by-path is unavailable.
    """
    by_node: dict[str, str] = {}  # resolved node -> stable path (first seen wins)
    if BY_PATH_DIR.is_dir():
        for link in sorted(BY_PATH_DIR.iterdir()):
            if not link.name.endswith(_CAPTURE_INTERFACE_SUFFIX):
                continue
            node = os.path.realpath(link)
            by_node.setdefault(node, str(link))
    if not by_node:
        for node in sorted(str(p) for p in Path("/dev").glob("video*")):
            by_node.setdefault(node, node)
    return sorted((stable, node) for node, stable in by_node.items())


def enumerate_cameras(*, probe: bool = True) -> list[DetectedCamera]:
    """List available capture cameras.

    With ``probe=True`` (default), each candidate is opened and one frame is
    read; only devices that actually yield a frame are returned, with their
    real resolution/fps. ``probe=False`` lists candidates without opening them
    (no resolution/fps), useful for tests and quick listings.
    """
    detected: list[DetectedCamera] = []
    for stable, node in _candidate_paths():
        if not probe:
            detected.append(DetectedCamera(len(detected), stable, node, 0, 0, 0.0))
            continue

        cap = cv2.VideoCapture(node, cv2.CAP_V4L2)
        try:
            if not cap.isOpened():
                logger.debug("skip %s: device not opened", node)
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.debug("skip %s: no frame produced", node)
                continue
            height, width = frame.shape[:2]
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            detected.append(
                DetectedCamera(len(detected), stable, node, int(width), int(height), fps)
            )
        finally:
            cap.release()
    return detected


def parse_v4l2_modes(text: str) -> tuple[CameraMode, ...]:
    """Parse ``v4l2-ctl --list-formats-ext`` output into capture modes.

    Groups each ``[n]: 'FOURCC'`` format with its ``Size: Discrete WxH`` entries
    and their ``Interval: ... (NN fps)`` lines.
    """
    modes: list[CameraMode] = []
    pixel_format: str | None = None
    resolution: Resolution | None = None
    fps: list[float] = []

    def commit() -> None:
        nonlocal resolution, fps
        if pixel_format is not None and resolution is not None:
            modes.append(CameraMode(pixel_format, resolution, tuple(fps)))
        resolution = None
        fps = []

    for line in text.splitlines():
        if (match := _FORMAT_RE.search(line)) is not None:
            commit()
            pixel_format = match.group(1)
        elif (match := _SIZE_RE.search(line)) is not None:
            commit()
            resolution = Resolution(int(match.group(1)), int(match.group(2)))
        elif (match := _FPS_RE.search(line)) is not None and resolution is not None:
            fps.append(float(match.group(1)))
    commit()
    return tuple(modes)


def list_modes(device_node: str) -> tuple[CameraMode, ...]:
    """Return the supported capture modes of a device via ``v4l2-ctl``."""
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device_node, "--list-formats-ext"],
            capture_output=True,
            text=True,
            timeout=_V4L2_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("v4l2-ctl failed for %s", device_node)
        return ()
    return parse_v4l2_modes(result.stdout)


def enumerate_camera_devices() -> list[CameraDevice]:
    """List capture cameras with their full set of supported modes (Phase 1).

    Only devices exposing at least one mode are returned. Identity uses the
    stable by-path symlink; the resolved ``/dev/videoN`` is the open target.
    """
    devices: list[CameraDevice] = []
    for stable, node in _candidate_paths():
        modes = list_modes(node)
        if not modes:
            logger.debug("skip %s: no modes reported", node)
            continue
        devices.append(CameraDevice(len(devices), stable, node, modes))
    return devices
