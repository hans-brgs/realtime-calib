"""Characterization of the per-camera capture loop (`_capture_frames`, lot 6 QLT-18).

Scripted frames 10 ms apart, a 30 fps camera: the capture grid keeps the first frame
of each 33.3 ms cell (0, 40, 70, 100... ms). These tests pin what the loop does with
the kept frames: publish, detect, record, report, and survive a bad frame.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.config import LiveKitConfig
from calibration_service.detection import BoardDetection
from calibration_service.models.frame import Frame
from calibration_service.session.manager import SessionManager
from calibration_service.transport import camera_publish_service
from calibration_service.transport.camera_publish_service import (
    TELEMETRY_TOPIC,
    CameraPublishService,
    _PublishTarget,
)

ORIGIN = 1000.0
KEPT_MS_OF_21 = [0, 40, 70, 100, 140, 170, 200]  # 21 frames, 10 ms apart, at 30 fps


class _Device:
    """Grabs scripted stamps ``step`` apart, every ``pace`` seconds of loop time (0 =
    instantly); parks once they run out."""

    def __init__(self, count: int, step: float = 0.010, pace: float = 0.0) -> None:
        self._stamps = [ORIGIN + k * step for k in range(count)]
        self._pace = pace
        self.retrieved: list[float] = []
        self._last = 0.0
        self.clock = "v4l2"

    async def grab(self) -> float | None:
        if not self._stamps:
            await asyncio.sleep(3600)
        if self._pace:
            await asyncio.sleep(self._pace)
        self._last = self._stamps.pop(0)
        return self._last

    async def retrieve(self) -> Frame:
        self.retrieved.append(self._last)
        return Frame(self._last, np.full((48, 64, 3), len(self.retrieved), np.uint8))

    async def release(self) -> None:
        pass


class _Publisher:
    def __init__(self) -> None:
        self.pushed: list[NDArray[np.uint8]] = []
        self.sent: list[str] = []
        self.disconnected = False

    def is_disconnected(self) -> bool:
        return self.disconnected

    def push(self, name: str, image: NDArray[np.uint8]) -> None:
        self.pushed.append(image)

    async def send_data(self, payload: str, topic: str) -> None:
        assert topic == TELEMETRY_TOPIC
        self.sent.append(payload)


class _Recorder:
    def __init__(self) -> None:
        self.frames: list[int] = []

    def write(self, image: NDArray[np.uint8]) -> None:
        self.frames.append(int(image[0, 0, 0]))


def _run(
    tmp_path: Path, device: _Device, publisher: _Publisher, setup: Any = None, seconds: float = 0.3
) -> CameraPublishService:
    async def scenario() -> CameraPublishService:
        service = CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))
        if setup is not None:
            setup(service)
        with ThreadPoolExecutor(max_workers=2) as pool:
            service._capture_executor = pool
            loop = asyncio.get_running_loop()
            target = _PublishTarget("cam_0", 0, "/dev/video0", 64, 48, 30)
            task = asyncio.create_task(
                service._capture_frames(loop, publisher, target, device)  # type: ignore[arg-type]
            )
            await asyncio.sleep(seconds)
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
        return service

    return asyncio.run(scenario())


def _kept_ms(device: _Device) -> list[int]:
    return [round((t - ORIGIN) * 1000) for t in device.retrieved]


def test_an_idle_camera_publishes_on_the_loop_clock_and_detects_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The capture grid follows the frame stamps; publication follows the loop clock
    # (ADR-0037, ADR-0049): an instant burst of kept frames publishes once, twice when
    # it straddles a 33 ms publication cell, never once per frame.
    detected: list[object] = []
    monkeypatch.setattr(camera_publish_service, "_detect_only", lambda *a: detected.append(a))
    monkeypatch.setattr(camera_publish_service, "_process_frame", lambda *a: detected.append(a))
    burst, publisher = _Device(21), _Publisher()
    _run(tmp_path, burst, publisher)
    assert _kept_ms(burst) == KEPT_MS_OF_21
    assert 1 <= len(publisher.pushed) <= 2 < len(burst.retrieved)
    # Frames arriving 40 ms apart in loop time: every kept frame is published.
    paced, publisher = _Device(10, step=0.040, pace=0.040), _Publisher()
    _run(tmp_path, paced, publisher, seconds=1.5)
    assert len(paced.retrieved) == 10
    assert len(publisher.pushed) == 10
    assert publisher.pushed[0].shape == (48, 64, 3)  # under the preview width: kept as is
    assert detected == [] and publisher.sent == []


def test_the_active_intrinsic_camera_records_every_kept_frame_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def detect_and_draw(_detector: object, image: NDArray[np.uint8], _size: object) -> Any:
        return image, BoardDetection.empty()

    monkeypatch.setattr(camera_publish_service, "_process_frame", detect_and_draw)
    monkeypatch.setattr(
        camera_publish_service, "_detect_only", lambda d, i, s: BoardDetection.empty()
    )
    recorder = _Recorder()

    def setup(service: CameraPublishService) -> None:
        service._build_detector = lambda **_k: object()  # type: ignore[assignment, method-assign]
        service._active_intrinsic = "cam_0"
        service._recorder = recorder  # type: ignore[assignment]

    device, publisher = _Device(21), _Publisher()
    _run(tmp_path, device, publisher, setup)
    # Every kept frame is written, in order (the mkv cadence is the capture cadence).
    assert recorder.frames == list(range(1, len(device.retrieved) + 1))
    # Telemetry leaves at most every 0.1 s of loop time; the scripted frames barely
    # move that clock, so one report covers the burst.
    assert 1 <= len(publisher.sent) <= 4
    assert '"phase": "intrinsic"' in publisher.sent[0]


def test_a_frame_that_fails_to_process_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = {"n": 0}
    real = camera_publish_service._downscale

    def flaky(image: NDArray[np.uint8], size: tuple[int, int]) -> NDArray[np.uint8]:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("encoder hiccup")
        return real(image, size)

    monkeypatch.setattr(camera_publish_service, "_downscale", flaky)
    device, publisher = _Device(10, step=0.040, pace=0.040), _Publisher()
    with caplog.at_level(
        logging.INFO, logger="calibration_service.transport.camera_publish_service"
    ):
        _run(tmp_path, device, publisher, seconds=1.5)
    assert len(device.retrieved) == 10  # the loop carried on
    assert len(publisher.pushed) == 9  # only the bad frame is lost
    assert "recovered after 1 failed frame(s)" in caplog.text


def test_a_disconnected_room_ends_the_loop(tmp_path: Path) -> None:
    device, publisher = _Device(21), _Publisher()
    publisher.disconnected = True
    _run(tmp_path, device, publisher, seconds=0.05)
    assert device.retrieved == []  # returned at once, without grabbing
