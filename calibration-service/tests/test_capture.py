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
        self._grabbed: NDArray[np.uint8] | None = None
        self.released = False

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]:
        if self._frames:
            return True, self._frames.pop(0)
        return False, None

    def grab(self) -> bool:
        self._grabbed = self._frames.pop(0) if self._frames else None
        return self._grabbed is not None

    def retrieve(self) -> tuple[bool, NDArray[np.uint8] | None]:
        if self._grabbed is None:
            return False, None
        return True, self._grabbed

    def get(self, prop_id: int) -> float:
        return 30.0

    def release(self) -> None:
        self.released = True


class RaisingSource:
    """A ``VideoSource`` whose reads raise, simulating a vanished USB camera."""

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]:
        raise RuntimeError("usb device disconnected")

    def grab(self) -> bool:
        raise RuntimeError("usb device disconnected")

    def retrieve(self) -> tuple[bool, NDArray[np.uint8] | None]:
        raise RuntimeError("usb device disconnected")

    def get(self, prop_id: int) -> float:
        return 0.0

    def release(self) -> None:
        return None


def _image() -> NDArray[np.uint8]:
    return np.zeros((4, 4, 3), dtype=np.uint8)


def test_read_returns_timestamped_frames() -> None:
    source = FakeSource([_image(), _image()])
    camera = CameraCapture(source, camera_index=2)

    first = camera.read()
    second = camera.read()

    assert first is not None
    assert second is not None
    assert second.timestamp >= first.timestamp
    assert first.image.shape == (4, 4, 3)


def test_read_returns_none_when_source_exhausted() -> None:
    camera = CameraCapture(FakeSource([]), camera_index=0)
    assert camera.read() is None


def test_read_returns_none_when_source_raises() -> None:
    camera = CameraCapture(RaisingSource(), camera_index=0)
    assert camera.read() is None


def test_grab_then_retrieve_decodes_the_grabbed_frame() -> None:
    source = FakeSource([_image(), _image()])
    camera = CameraCapture(source, camera_index=3)

    assert camera.grab() is not None  # the frame's timestamp
    frame = camera.retrieve()

    assert frame is not None
    assert frame.image.shape == (4, 4, 3)


def test_grab_returns_none_when_source_exhausted() -> None:
    camera = CameraCapture(FakeSource([]), camera_index=0)
    assert camera.grab() is None
    assert camera.retrieve() is None


def test_grab_returns_none_when_source_raises() -> None:
    camera = CameraCapture(RaisingSource(), camera_index=0)
    assert camera.grab() is None
    assert camera.retrieve() is None


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


def test_enumeration_never_reopens_a_node_the_service_holds(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # A second open of a streaming device fails with EBUSY (QLT-8): a held node is
    # listed from what the caller knows, and keeps its slot so indices don't shift.
    monkeypatch.setattr(
        enumeration,
        "_candidate_paths",
        lambda: [("/dev/v4l/by-path/a", "/dev/video0"), ("/dev/v4l/by-path/b", "/dev/video2")],
    )
    opened: list[str] = []

    class _Probe:
        def __init__(self, node: str, _api: int) -> None:
            opened.append(node)

        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, NDArray[np.uint8]]:
            return True, np.zeros((720, 1280, 3), np.uint8)

        def get(self, _prop: int) -> float:
            return 30.0

        def release(self) -> None:
            pass

    monkeypatch.setattr("calibration_service.capture.enumeration.cv2.VideoCapture", _Probe)
    cameras = enumeration.enumerate_cameras(known={"/dev/video0": (1920, 1080, 30.0)})
    assert opened == ["/dev/video2"]
    assert [(c.index, c.device_node, c.width) for c in cameras] == [
        (0, "/dev/video0", 1920),
        (1, "/dev/video2", 1280),
    ]
