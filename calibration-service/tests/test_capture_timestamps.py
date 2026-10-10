"""Frames carry the V4L2 buffer timestamp, on a base chosen once per open (ADR-0049)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic import _warn_on_mixed_clocks
from calibration_service.capture.camera import CameraCapture
from calibration_service.capture.device import CameraDevice
from calibration_service.config import LiveKitConfig
from calibration_service.detection import BoardDetection
from calibration_service.models.frame import Frame
from calibration_service.session.manager import SessionManager
from calibration_service.transport import camera_publish_service
from calibration_service.transport.camera_publish_service import (
    CameraPublishService,
    _PublishTarget,
)


class _StampedSource:
    """A source whose CAP_PROP_POS_MSEC is scripted, frame by frame (None = 0)."""

    def __init__(self, kernel_ms: Sequence[float | None]) -> None:
        self._kernel_ms = list(kernel_ms)
        self._current: float | None = None

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, NDArray[np.uint8] | None]:
        return True, np.zeros((4, 4, 3), np.uint8)

    def grab(self) -> bool:
        if not self._kernel_ms:
            return False
        self._current = self._kernel_ms.pop(0)
        return True

    def retrieve(self) -> tuple[bool, NDArray[np.uint8] | None]:
        return True, np.zeros((4, 4, 3), np.uint8)

    def get(self, prop_id: int) -> float:
        assert prop_id == cv2.CAP_PROP_POS_MSEC
        return 0.0 if self._current is None else self._current

    def release(self) -> None:
        pass


def _now_ms(offset_s: float = 0.0) -> float:
    return (time.monotonic() + offset_s) * 1000.0


def test_valid_kernel_stamps_at_open_select_the_kernel_base() -> None:
    base = _now_ms(-0.2)  # scripted frames 20 ms apart, all in the past
    camera = CameraCapture(_StampedSource([base, base + 20, base + 40, base + 60]), 0)
    assert camera.choose_clock() == "v4l2"
    stamp = camera.grab()
    assert stamp == pytest.approx((base + 60) / 1000.0)  # the kernel's, not now
    frame = camera.retrieve()
    assert frame is not None and frame.timestamp == stamp  # stamped at grab


def test_an_unusable_stamp_at_open_keeps_the_host_base_for_the_whole_capture() -> None:
    later = _now_ms(-0.01)
    camera = CameraCapture(_StampedSource([None, None, None, later, later + 20]), 0)
    assert camera.choose_clock() == "host"
    before = time.monotonic()
    stamp = camera.grab()  # a valid kernel stamp now: the base does not switch
    assert stamp is not None and before <= stamp <= time.monotonic()


def test_a_stamp_from_another_clock_is_refused_at_open() -> None:
    # e.g. uvcvideo clock=realtime, or a time namespace: far from our monotonic now.
    epoch_ms = time.time() * 1000.0
    camera = CameraCapture(_StampedSource([epoch_ms, epoch_ms + 20, epoch_ms + 40]), 0)
    assert camera.choose_clock() == "host"


def test_a_non_increasing_stamp_drops_the_frame_and_keeps_the_base() -> None:
    base = _now_ms(-0.2)
    stamps = [base, base + 20, base + 40, base + 60, base + 60, base + 80]
    camera = CameraCapture(_StampedSource(stamps), 0)
    assert camera.choose_clock() == "v4l2"
    assert camera.grab() == pytest.approx((base + 60) / 1000.0)
    assert camera.grab() is None  # same stamp again: dropped, neither written nor detected
    assert camera.dropped == 1
    assert camera.grab() == pytest.approx((base + 80) / 1000.0)
    assert camera.clock == "v4l2"


def test_a_frame_old_after_a_stall_keeps_its_kernel_stamp() -> None:
    # The very defect being fixed (SYN-3): a frame that waited in the driver's
    # queue during a 2 s stall must keep its own instant, not get "now".
    base = _now_ms(-0.2)
    camera = CameraCapture(_StampedSource([base, base + 20, base + 40]), 0)
    assert camera.choose_clock() == "v4l2"
    camera._source._kernel_ms.append(base + 60)  # type: ignore[attr-defined]
    time.sleep(0.05)
    stamp = camera.grab()
    assert stamp is not None and stamp == pytest.approx((base + 60) / 1000.0)
    assert time.monotonic() - stamp > 0.05


class _ScriptedDevice:
    """Grabs return scripted stamps 10 ms apart, instantly: the loop clock ignores them."""

    def __init__(self, stamps: list[float]) -> None:
        self._stamps = list(stamps)
        self.retrieved: list[float] = []
        self._last = 0.0
        self.clock = "v4l2"

    async def grab(self) -> float | None:
        if not self._stamps:
            await asyncio.sleep(3600)  # parked until the test cancels the loop
        self._last = self._stamps.pop(0)
        return self._last

    async def retrieve(self) -> Frame:
        self.retrieved.append(self._last)
        return Frame(0, 1, self._last, np.zeros((48, 64, 3), np.uint8))

    async def release(self) -> None:
        pass


class _Publisher:
    def is_disconnected(self) -> bool:
        return False

    def push(self, name: str, image: object) -> None:
        pass

    async def send_data(self, payload: str, topic: str) -> None:
        pass


def test_the_capture_grid_follows_the_frame_timestamps(tmp_path: Path) -> None:
    # 10 ms apart against 33.3 ms cells (30 fps): the first frame of each cell is
    # kept — 0, 40, 70, 100 ms — whatever the loop clock says (it barely moves).
    origin = 1000.0
    device = _ScriptedDevice([origin + k * 0.010 for k in range(11)])

    async def scenario() -> None:
        service = CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            service._capture_executor = pool
            loop = asyncio.get_running_loop()
            target = _PublishTarget("cam_0", 0, "/dev/video0", 64, 48, 30)
            task = asyncio.create_task(
                service._capture_frames(loop, _Publisher(), target, device)  # type: ignore[arg-type]
            )
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    kept_ms = [round((t - origin) * 1000) for t in device.retrieved]
    assert kept_ms == [0, 40, 70, 100]


def test_camera_device_reports_the_chosen_base() -> None:
    base = _now_ms(-0.2)

    async def scenario() -> str:
        device = CameraDevice("cam_0")
        await device.open(lambda: CameraCapture(_StampedSource([base, base + 20, base + 40]), 0))
        assert device.clock == "host"  # until chosen
        chosen = await device.choose_clock()
        await device.release()
        return chosen

    assert asyncio.run(scenario()) == "v4l2"


def test_the_compute_warns_when_the_cameras_used_different_bases(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def manifest(*clocks: str) -> None:
        cameras = [{"name": f"cam_{i}", "clock": clock} for i, clock in enumerate(clocks)]
        (tmp_path / "manifest.json").write_text(json.dumps({"cameras": cameras}))

    with caplog.at_level(logging.WARNING):
        manifest("v4l2", "v4l2", "unknown")
        _warn_on_mixed_clocks(tmp_path)
        assert "different time bases" not in caplog.text
        manifest("v4l2", "host")
        _warn_on_mixed_clocks(tmp_path)
        assert "different time bases" in caplog.text
        caplog.clear()
        manifest("mixed", "v4l2")
        _warn_on_mixed_clocks(tmp_path)
        assert "different time bases" in caplog.text


def test_the_detection_grid_follows_the_frame_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Frames 10 ms apart, kept on the 30 Hz grid (0, 40, 70, 100, 140, 170, 200 ms);
    # the 15 Hz extrinsic detection grid takes the first of each of ITS cells:
    # 0, 70, 140, 200 ms. On loop time (which barely moves) it took one frame only.
    origin = 1000.0
    device = _ScriptedDevice([origin + k * 0.010 for k in range(21)])
    detected: list[float] = []

    def detect(*_args: object) -> BoardDetection:
        detected.append(device._last)
        return BoardDetection.empty()

    def detect_and_draw(*_args: object) -> tuple[NDArray[np.uint8], BoardDetection]:
        return np.zeros((48, 64, 3), np.uint8), detect()

    monkeypatch.setattr(camera_publish_service, "_detect_only", detect)
    monkeypatch.setattr(camera_publish_service, "_process_frame", detect_and_draw)

    async def scenario() -> None:
        service = CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))
        service._build_detector = lambda **_k: object()  # type: ignore[assignment, method-assign]
        service.set_active_view("extrinsic")  # every camera detects, at 15 Hz
        with ThreadPoolExecutor(max_workers=2) as pool:
            service._capture_executor = pool
            loop = asyncio.get_running_loop()
            target = _PublishTarget("cam_0", 0, "/dev/video0", 64, 48, 30)
            task = asyncio.create_task(
                service._capture_frames(loop, _Publisher(), target, device)  # type: ignore[arg-type]
            )
            await asyncio.sleep(0.3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert [round((t - origin) * 1000) for t in detected] == [0, 70, 140, 200]


def test_the_clock_choice_stops_at_the_first_missing_frame() -> None:
    # Each failed grab may last a whole select() timeout: one is enough to decide.
    camera = CameraCapture(_StampedSource([_now_ms(-0.2)]), 0)  # one frame, then nothing
    started = time.monotonic()
    assert camera.choose_clock() == "host"
    assert time.monotonic() - started < 0.5
