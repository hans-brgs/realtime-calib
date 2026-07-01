"""Camera device + supported capture modes (from V4L2 enumeration)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Resolution:
    """A capture resolution in pixels."""

    width: int
    height: int


@dataclass(frozen=True)
class CameraMode:
    """One supported capture mode: a pixel format at a resolution, with its fps options."""

    pixel_format: str  # FOURCC, e.g. "MJPG" or "YUYV"
    resolution: Resolution
    fps: tuple[float, ...]


@dataclass(frozen=True)
class CameraDevice:
    """A detected camera with its full set of supported modes (pre-config)."""

    index: int  # provisional ordering index (not yet the logical/anchor index)
    device_path: str  # stable identity (by-path symlink when available)
    device_node: str  # resolved /dev/videoN, used to open the device
    modes: tuple[CameraMode, ...]
