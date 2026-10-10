"""Nothing blocks the event loop while cameras open (audit QLT-2/QLT-7)."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from calibration_service.capture.device import CameraDevice
from calibration_service.config import LiveKitConfig
from calibration_service.models.frame import Frame
from calibration_service.session.manager import SessionManager
from calibration_service.transport import camera_publish_service
from calibration_service.transport.camera_publish_service import (
    CameraPublishService,
    _PublishTarget,
)


class _SlowCamera:
    clock = "host"

    def choose_clock(self) -> str:
        return self.clock

    def read(self) -> Frame:
        return Frame(time.monotonic(), np.zeros((48, 64, 3), np.uint8))

    def release(self) -> None:
        pass


class _Publisher:
    async def send_data(self, payload: str, topic: str) -> None:
        pass

    def push(self, name: str, image: object) -> None:
        pass

    def unmute(self, name: str) -> None:
        pass

    def mute(self, name: str) -> None:
        pass

    def is_disconnected(self) -> bool:
        return True  # the capture loop ends at once


def test_a_slow_camera_open_does_not_freeze_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # open_camera takes 0.26-0.33 s on the rig (v4l2-ctl + VideoCapture + set).
    def slow_open(*_args: object, **_kwargs: object) -> _SlowCamera:
        time.sleep(0.3)
        return _SlowCamera()

    monkeypatch.setattr(camera_publish_service, "open_camera", slow_open)

    async def scenario() -> float:
        service = CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))
        loop = asyncio.get_running_loop()
        lags: list[float] = []

        async def ticker() -> None:
            while True:
                before = loop.time()
                await asyncio.sleep(0.01)
                lags.append(loop.time() - before - 0.01)

        ticking = asyncio.create_task(ticker())
        target = _PublishTarget("cam_0", 0, "/dev/video0", 64, 48, 30, configured=True)
        opened = await service._start_capture(loop, _Publisher(), target)  # type: ignore[arg-type]
        assert opened is not None
        device, task = opened
        await task
        await device.release()
        ticking.cancel()
        return max(lags)

    assert asyncio.run(scenario()) < 0.05  # was >= 0.3 s with the open on the loop


def test_the_session_is_read_on_the_loop_and_the_probe_runs_off_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = SessionManager(tmp_path, "default")  # no cameras yet: identification probe
    service = CameraPublishService(LiveKitConfig(), manager)
    threads: dict[str, int] = {}
    original = manager.current_or_none

    def current_or_none():  # type: ignore[no-untyped-def]
        threads["session"] = threading.get_ident()
        return original()

    def probe(**_kwargs: object) -> list[object]:
        threads["probe"] = threading.get_ident()
        return []

    monkeypatch.setattr(manager, "current_or_none", current_or_none)
    monkeypatch.setattr(camera_publish_service, "enumerate_cameras", probe)

    async def scenario() -> int:
        await service._resolve_targets({})
        return threading.get_ident()

    loop_thread = asyncio.run(scenario())
    assert threads["session"] == loop_thread  # QLT-7: never from a worker thread
    assert threads["probe"] != loop_thread  # the probe opens devices: off the loop


def test_a_held_camera_is_passed_to_the_probe_not_reopened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))
    seen: dict[str, object] = {}

    def probe(**kwargs: object) -> list[object]:
        seen.update(kwargs)
        return []

    monkeypatch.setattr(camera_publish_service, "enumerate_cameras", probe)
    held = {"cam_0": _PublishTarget("cam_0", 0, "/dev/video2", 1920, 1080, 30)}
    asyncio.run(service._resolve_targets(held))
    assert seen["known"] == {"/dev/video2": (1920, 1080, 30.0)}


def test_camera_device_keeps_a_cancelled_open_for_its_release() -> None:
    # The handle is stored by the function on the device thread: an open whose
    # coroutine is cancelled still leaves the release a camera to close.
    released = threading.Event()

    class _Camera:
        def release(self) -> None:
            released.set()

    def opener() -> _Camera:
        time.sleep(0.05)
        return _Camera()

    async def scenario() -> None:
        device = CameraDevice("cam_0")
        opening = asyncio.create_task(device.open(opener))  # type: ignore[arg-type]
        await asyncio.sleep(0.01)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        await device.release()

    asyncio.run(scenario())
    assert released.is_set()


def test_the_first_frame_wait_ends_at_its_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A camera that opens but never streams: each read used to be one of 60
    # attempts of a whole select() timeout; now the wait ends at a deadline.
    monkeypatch.setattr(camera_publish_service, "_FIRST_FRAME_DEADLINE_S", 0.3)

    class _Silent:
        clock = "host"
        released = False

        def read(self) -> None:
            time.sleep(0.05)  # stands for a select() timeout
            return None

        def release(self) -> None:
            self.released = True

    camera = _Silent()
    monkeypatch.setattr(camera_publish_service, "open_camera", lambda *_a, **_k: camera)

    async def scenario() -> float:
        service = CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))
        loop = asyncio.get_running_loop()
        target = _PublishTarget("cam_0", 0, "/dev/video0", 64, 48, 30, configured=True)
        started = time.monotonic()
        assert await service._start_capture(loop, _Publisher(), target) is None  # type: ignore[arg-type]
        return time.monotonic() - started

    elapsed = asyncio.run(scenario())
    assert elapsed < 1.0
    assert camera.released
