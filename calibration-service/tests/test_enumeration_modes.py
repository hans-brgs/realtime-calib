"""Tests for V4L2 mode parsing (no hardware needed)."""

from __future__ import annotations

from calibration_service.capture.enumeration import parse_v4l2_modes
from calibration_service.models.camera import CameraMode, Resolution

_SAMPLE = """ioctl: VIDIOC_ENUM_FMT
\tType: Video Capture

\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 640x480
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t\t\tInterval: Discrete 0.040s (25.000 fps)
\t\tSize: Discrete 1280x720
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t[1]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 640x480
\t\t\tInterval: Discrete 0.033s (30.000 fps)
"""


def test_parse_groups_formats_sizes_and_fps() -> None:
    modes = parse_v4l2_modes(_SAMPLE)

    assert modes == (
        CameraMode("MJPG", Resolution(640, 480), (30.0, 25.0)),
        CameraMode("MJPG", Resolution(1280, 720), (30.0,)),
        CameraMode("YUYV", Resolution(640, 480), (30.0,)),
    )


def test_parse_empty_returns_no_modes() -> None:
    assert parse_v4l2_modes("") == ()
    assert parse_v4l2_modes("garbage without any format line") == ()


def test_parse_format_without_size_is_skipped() -> None:
    text = "\t[0]: 'MJPG' (Motion-JPEG, compressed)\n"
    assert parse_v4l2_modes(text) == ()
