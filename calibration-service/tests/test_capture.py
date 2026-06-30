"""Unit tests for the capture layer, using a fake video source (no hardware)."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from calibration_service.capture import enumeration
from calibration_service.capture.camera import CameraCapture


class FakeSource:
    """In-memory ``VideoSource`` that yields a fixed list of frames."""

    def __init__(self, frames: list[NDArray[np.uint8]]) -> None:
        self._frames = list(frames)
        self.released = False

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]:
        if self._frames:
            return True, self._frames.pop(0)
        return False, None

    def get(self, prop_id: int) -> float:
        return 30.0

    def release(self) -> None:
        self.released = True


class RaisingSource:
    """A ``VideoSource`` whose ``read`` raises, simulating a vanished USB camera."""

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]:
        raise RuntimeError("usb device disconnected")

    def get(self, prop_id: int) -> float:
        return 0.0

    def release(self) -> None:
        return None


def _image() -> NDArray[np.uint8]:
    return np.zeros((4, 4, 3), dtype=np.uint8)


def test_read_increments_frame_id_and_sets_fields() -> None:
    source = FakeSource([_image(), _image()])
    camera = CameraCapture(source, camera_index=2)

    first = camera.read()
    second = camera.read()

    assert first is not None
    assert second is not None
    assert first.camera_index == 2
    assert (first.frame_id, second.frame_id) == (1, 2)
    assert second.timestamp >= first.timestamp
    assert first.image.shape == (4, 4, 3)


def test_read_returns_none_when_source_exhausted() -> None:
    camera = CameraCapture(FakeSource([]), camera_index=0)
    assert camera.read() is None


def test_read_returns_none_when_source_raises() -> None:
    camera = CameraCapture(RaisingSource(), camera_index=0)
    assert camera.read() is None


def test_context_manager_releases_source() -> None:
    source = FakeSource([])
    with CameraCapture(source, camera_index=0):
        pass
    assert source.released is True


def test_enumerate_cameras_handles_no_candidates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(enumeration, "_candidate_paths", lambda: [])
    assert enumeration.enumerate_cameras() == []


def test_enumerate_cameras_without_probe_lists_candidates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        enumeration,
        "_candidate_paths",
        lambda: [("/dev/v4l/by-path/cam-a", "/dev/video0")],
    )
    cameras = enumeration.enumerate_cameras(probe=False)
    assert len(cameras) == 1
    assert cameras[0].device_path == "/dev/v4l/by-path/cam-a"
    assert cameras[0].device_node == "/dev/video0"
    assert cameras[0].index == 0
