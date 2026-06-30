"""USB camera enumeration via V4L2 device nodes.

Phase 0 keeps this minimal: list capture devices, probe each by reading one
frame, report the default resolution/fps. Full mode enumeration (all supported
resolutions/fps via V4L2) belongs to the camera-detection-config feature
(Phase 1). Device identity favours the stable ``/dev/v4l/by-path`` symlink over
``/dev/videoN`` (Camera entity invariant).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import cv2

logger = logging.getLogger(__name__)

BY_PATH_DIR = Path("/dev/v4l/by-path")

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
