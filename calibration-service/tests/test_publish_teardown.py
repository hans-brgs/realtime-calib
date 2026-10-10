"""Capture teardown under cancellation (ADR-0050, audit QLT-1/SYN-2).

A V4L2 handle released while its grab still runs wedges the device node, and a
writer closed mid-write truncates the sweep. These tests hold a device call open
on its thread, cancel around it, and check the order of what really happened.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from calibration_service.capture.device import CameraDevice
from calibration_service.concurrency import settle
from calibration_service.config import LiveKitConfig
from calibration_service.models.frame import Frame
from calibration_service.models.session import CameraConfig
from calibration_service.session.manager import SessionManager
from calibration_service.transport import camera_publish_service
from calibration_service.transport.camera_publish_service import (
    CameraPublishService,
    _PublishTarget,
)

WIDTH, HEIGHT = 64, 48


def _frame() -> Frame:
    return Frame(time.monotonic(), np.zeros((HEIGHT, WIDTH, 3), np.uint8))


class _Capture:
    """A fake camera whose calls can be held open on the device thread.

    ``events`` records what ran, in order; ``hold`` blocks the next grab (or the
    first read) until it is set, and ``busy`` tells the test that a call is held.
    """

    def __init__(self, name: str, events: list[tuple[str, str]]) -> None:
        self.name = name
        self.events = events
        self.hold: threading.Event | None = None
        self.hold_read = False
        self.busy = threading.Event()
        self.released = False
        self.clock = "host"

    def _held(self, what: str) -> None:
        self.events.append((self.name, f"{what} start"))
        if self.hold is not None and not self.hold.is_set():
            self.busy.set()
            self.hold.wait(5)
        time.sleep(0.002)
        self.events.append((self.name, f"{what} end"))

    def read(self) -> Frame:
        if self.hold_read:
            self._held("read")
        return _frame()

    def choose_clock(self) -> str:
        return self.clock

    def grab(self) -> float:
        self._held("grab")
        return time.monotonic()

    def retrieve(self) -> Frame:
        return _frame()

    def release(self) -> None:
        self.events.append((self.name, "release"))
        self.released = True


class _Publisher:
    """The LiveKit surface the capture path touches; push can be made to fail."""

    def __init__(self) -> None:
        self.pushed = 0
        self.fail_push: Callable[[int], bool] = lambda _n: False
        self.closed = False

    async def connect(self, url: str, token: str) -> None:
        pass

    async def await_connected(self, timeout: float = 40.0) -> bool:
        return True

    def is_disconnected(self) -> bool:
        return False

    async def publish_camera_track(self, name: str, width: int, height: int, fps: int) -> None:
        pass

    def push(self, name: str, image: object) -> None:
        self.pushed += 1
        if self.fail_push(self.pushed):
            raise RuntimeError("push failed")

    def mute(self, name: str) -> None:
        pass

    def unmute(self, name: str) -> None:
        pass

    async def send_data(self, payload: str, topic: str) -> None:
        pass

    async def aclose(self) -> None:
        self.closed = True


def _service(tmp_path: Path) -> CameraPublishService:
    return CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))


def _target(name: str = "cam_0") -> _PublishTarget:
    return _PublishTarget(name, 0, f"/dev/{name}", WIDTH, HEIGHT, 30, configured=True)


async def _wait_for(flag: threading.Event) -> None:
    for _ in range(500):
        if flag.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the held call never started")


async def _open(capture: _Capture) -> CameraDevice:
    device = CameraDevice(capture.name)
    await device.open(lambda: capture)  # type: ignore[arg-type, return-value]
    return device


def _order(events: list[tuple[str, str]], name: str) -> list[str]:
    return [what for who, what in events if who == name]


def _run_bounded(scenario: Callable[[], Coroutine[Any, Any, None]], seconds: float = 10.0) -> None:
    """``asyncio.run`` in a daemon thread: a teardown that hangs fails THIS test.

    A stop that never ends cannot be cancelled either (it settles what it
    stops), so within the test's own loop it would hang the whole suite.
    """
    errors: list[BaseException] = []

    def target() -> None:
        try:
            asyncio.run(scenario())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        pytest.fail(f"the scenario hung for {seconds:.0f} s")
    if errors:
        raise errors[0]


def test_a_loop_cancelled_mid_grab_releases_only_after_the_grab(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        events: list[tuple[str, str]] = []
        capture = _Capture("cam_0", events)
        capture.hold = threading.Event()
        device = await _open(capture)
        with ThreadPoolExecutor(max_workers=2) as pool:
            service._capture_executor = pool
            loop = asyncio.get_running_loop()
            task = asyncio.create_task(
                service._capture_loop(loop, _Publisher(), _target(), device)  # type: ignore[arg-type]
            )
            await _wait_for(capture.busy)
            task.cancel()
            await asyncio.sleep(0.05)
            assert "release" not in _order(events, "cam_0")  # the grab still holds the device
            capture.hold.set()
            await settle([task])
        assert _order(events, "cam_0")[-3:] == ["grab start", "grab end", "release"]

    asyncio.run(scenario())


def test_two_cancellations_during_a_stop_still_release_and_are_not_swallowed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        events: list[tuple[str, str]] = []
        capture = _Capture("cam_0", events)
        capture.hold = threading.Event()
        device = await _open(capture)
        with ThreadPoolExecutor(max_workers=2) as pool:
            service._capture_executor = pool
            loop = asyncio.get_running_loop()
            publisher = _Publisher()
            task = asyncio.create_task(
                service._capture_loop(loop, publisher, _target(), device)  # type: ignore[arg-type]
            )
            await _wait_for(capture.busy)
            stopper = asyncio.create_task(
                service._stop_capture(publisher, "cam_0", task, device)  # type: ignore[arg-type]
            )
            await asyncio.sleep(0.01)
            stopper.cancel()
            await asyncio.sleep(0.01)
            stopper.cancel()
            capture.hold.set()
            await settle([stopper])
        assert stopper.cancelled()  # the caller's cancellation came back out
        assert capture.released
        assert _order(events, "cam_0")[-2:] == ["grab end", "release"]

    asyncio.run(scenario())


class _HeldRecorder:
    """An extrinsic recorder whose write blocks; records write/close order."""

    def __init__(self, events: list[tuple[str, str]]) -> None:
        self.events = events
        self.hold = threading.Event()
        self.writing = threading.Event()

    def write(self, name: str, image: object, timestamp: float, clock: str = "host") -> None:
        self.events.append(("recorder", "write start"))
        self.writing.set()
        self.hold.wait(5)
        self.events.append(("recorder", "write end"))

    def close(self) -> dict[str, int]:
        self.events.append(("recorder", "close"))
        return {"cam_0": 1}


def test_two_cancellations_during_a_write_never_close_the_writer_mid_write(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        events: list[tuple[str, str]] = []
        device = await _open(_Capture("cam_0", events))
        recorder = _HeldRecorder(events)
        service._extrinsic = recorder  # type: ignore[assignment]
        service._extrinsic_locks = {"cam_0": asyncio.Lock()}
        with ThreadPoolExecutor(max_workers=2) as pool:
            service._capture_executor = pool
            loop = asyncio.get_running_loop()
            task = asyncio.create_task(
                service._capture_loop(loop, _Publisher(), _target(), device)  # type: ignore[arg-type]
            )
            await _wait_for(recorder.writing)
            stopping = asyncio.create_task(service.stop_extrinsic_recording())
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0.05)
            assert ("recorder", "close") not in events  # the write still runs
            recorder.hold.set()
            await settle([task, stopping])
        writes = _order(events, "recorder")
        assert writes.index("close") > writes.index("write end")

    asyncio.run(scenario())


def _configure(manager: SessionManager, *names: str) -> None:
    manager.current().cameras = [
        CameraConfig(
            index=i,
            name=name,
            prefix="cam",
            device_path=f"/dev/v4l/by-path/{name}",
            device_node=f"/dev/{name}",
            width=WIDTH,
            height=HEIGHT,
            resize_factor=1.0,
            fps=30,
        )
        for i, name in enumerate(names)
    ]


async def _live_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *names: str
) -> tuple[CameraPublishService, dict[str, _Capture], list[tuple[str, str]], _Publisher]:
    """A started service with real capture loops on fake cameras and a fake LiveKit."""
    events: list[tuple[str, str]] = []
    captures = {f"/dev/{name}": _Capture(name, events) for name in names}
    publisher = _Publisher()
    monkeypatch.setattr(camera_publish_service, "LiveKitPublisher", lambda: publisher)
    monkeypatch.setattr(camera_publish_service, "mint_publish_token", lambda *_a, **_k: "t")
    monkeypatch.setattr(
        camera_publish_service, "open_camera", lambda node, *_a, **_k: captures[node]
    )
    monkeypatch.setattr(camera_publish_service, "_OPEN_STAGGER_S", 0.0)
    manager = SessionManager(tmp_path, "default")
    _configure(manager, *names)
    service = CameraPublishService(LiveKitConfig(), manager)
    await service.start()
    for _ in range(300):
        if all(_order(events, name).count("grab end") >= 2 for name in names):
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("the cameras never went live")
    return service, {c.name: c for c in captures.values()}, events, publisher


def test_a_stop_landing_during_a_camera_close_ends_quickly_and_releases_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, captures, events, _publisher = await _live_service(
            tmp_path, monkeypatch, "cam_0", "cam_1"
        )
        held = captures["cam_0"]
        held.hold = threading.Event()
        await _wait_for(held.busy)
        # cam_0 leaves the live set: its stop now waits on the held grab...
        service.set_active_view("intrinsic")
        service.set_active_intrinsic("cam_1")
        await asyncio.sleep(0.05)
        # ...and stop() lands in the middle of that close.
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0.05)
        held.hold.set()
        started = time.monotonic()
        await stopping  # it used to hang forever
        assert time.monotonic() - started < 1.0
        assert all(capture.released for capture in captures.values())
        assert _order(events, "cam_0")[-2:] == ["grab end", "release"]

    _run_bounded(scenario)


def test_a_loop_cancelled_before_its_first_step_still_gets_its_device_released(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        capture = _Capture("cam_0", [])
        device = await _open(capture)
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(
            service._capture_loop(loop, _Publisher(), _target(), device)  # type: ignore[arg-type]
        )
        task.cancel()  # before it ever ran: its finally never will
        await service._stop_capture(_Publisher(), "cam_0", task, device)  # type: ignore[arg-type]
        assert task.cancelled()
        assert capture.released

    asyncio.run(scenario())


def test_a_cancellation_during_the_first_frame_wait_releases_after_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, str]] = []
    capture = _Capture("cam_0", events)
    capture.hold = threading.Event()
    capture.hold_read = True
    monkeypatch.setattr(camera_publish_service, "open_camera", lambda *_a, **_k: capture)

    async def scenario() -> None:
        service = _service(tmp_path)
        loop = asyncio.get_running_loop()
        starting = asyncio.create_task(
            service._start_capture(loop, _Publisher(), _target())  # type: ignore[arg-type]
        )
        await _wait_for(capture.busy)
        starting.cancel()
        await asyncio.sleep(0.02)
        assert not capture.released  # the read still holds the device
        assert capture.hold is not None
        capture.hold.set()
        await settle([starting])
        assert starting.cancelled()
        assert _order(events, "cam_0") == ["read start", "read end", "release"]

    asyncio.run(scenario())


def test_a_frame_whose_processing_fails_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, captures, _events, publisher = await _live_service(tmp_path, monkeypatch, "cam_0")
        before = publisher.pushed
        publisher.fail_push = lambda n: before < n <= before + 3  # three frames fail
        await asyncio.sleep(0.3)
        assert publisher.pushed > before + 3  # still publishing after the failures
        assert (
            service._health.snapshot(asyncio.get_running_loop().time())["cam_0"]["state"] == "live"
        )
        await service.stop()
        assert captures["cam_0"].released

    _run_bounded(scenario)


def test_a_frame_processing_that_keeps_failing_ends_the_loop_for_a_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(camera_publish_service, "_CAMERA_LOST_AFTER_S", 0.1)

    async def scenario() -> None:
        service = _service(tmp_path)
        device = await _open(_Capture("cam_0", []))
        publisher = _Publisher()
        publisher.fail_push = lambda _n: True
        with ThreadPoolExecutor(max_workers=2) as pool:
            service._capture_executor = pool
            loop = asyncio.get_running_loop()
            with pytest.raises(RuntimeError, match="push failed"):
                await asyncio.wait_for(
                    service._capture_loop(loop, publisher, _target(), device),  # type: ignore[arg-type]
                    timeout=2.0,
                )
        assert device.released

    asyncio.run(scenario())


def test_a_failing_sweep_close_still_stops_every_camera_and_the_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service, captures, _events, publisher = await _live_service(
            tmp_path, monkeypatch, "cam_0", "cam_1"
        )

        async def broken_stop() -> dict[str, int]:
            raise OSError("disk full")

        monkeypatch.setattr(service, "stop_extrinsic_recording", broken_stop)
        await service.stop()
        assert all(capture.released for capture in captures.values())
        assert publisher.closed

    _run_bounded(scenario)


def test_a_camera_that_fails_to_start_does_not_end_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = _Capture("cam_0", [])
    monkeypatch.setattr(camera_publish_service, "open_camera", lambda *_a, **_k: capture)

    async def scenario() -> None:
        service = _service(tmp_path)
        publisher = _Publisher()
        publisher.fail_push = lambda _n: True  # the first frame's push raises
        loop = asyncio.get_running_loop()
        open_cams: dict[str, tuple[CameraDevice, asyncio.Task[None], _PublishTarget]] = {}
        # Raising out of the reconcile would reconnect LiveKit (ADR-0029): it must not.
        await service._reconcile_open_set(loop, publisher, {"cam_0": _target()}, open_cams)  # type: ignore[arg-type]
        assert open_cams == {}
        assert capture.released
        state = service._health.snapshot(loop.time())["cam_0"]
        assert (state["state"], state["reason"]) == ("error", "could not start capture")

    asyncio.run(scenario())


def test_a_connection_that_never_completes_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # QLT-9: the half-open connection used to be published on anyway.
    class _Unconnected(_Publisher):
        async def await_connected(self, timeout: float = 40.0) -> bool:
            return False

    publisher = _Unconnected()
    opened: list[str] = []
    monkeypatch.setattr(camera_publish_service, "LiveKitPublisher", lambda: publisher)
    monkeypatch.setattr(camera_publish_service, "mint_publish_token", lambda *_a, **_k: "t")
    monkeypatch.setattr(
        camera_publish_service, "open_camera", lambda node, *_a, **_k: opened.append(node)
    )

    async def scenario() -> None:
        manager = SessionManager(tmp_path, "default")
        _configure(manager, "cam_0")
        service = CameraPublishService(LiveKitConfig(), manager)
        await service.start()
        await asyncio.sleep(0.2)
        await service.stop()

    _run_bounded(scenario)
    assert opened == []  # no camera opened over a dead connection
    assert publisher.closed  # and the connection was closed for the retry
