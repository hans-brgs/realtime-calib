"""On-demand capture reconciliation: the view -> live-camera-set mapping (ADR-0021)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from calibration_service.config import LiveKitConfig
from calibration_service.session.manager import SessionManager
from calibration_service.transport.camera_publish_service import (
    CameraPublishService,
    _PublishTarget,
)


def _service(tmp_path: Path) -> CameraPublishService:
    return CameraPublishService(LiveKitConfig(), SessionManager(tmp_path))


class _FakePublisher:
    def __init__(self) -> None:
        self.muted: list[str] = []

    def mute(self, name: str) -> None:
        self.muted.append(name)


class _FakeCamera:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class _FakeRecorder:
    def __init__(self) -> None:
        self.closed = False
        self.frames = 3

    def close(self) -> None:
        self.closed = True


async def _done_task() -> asyncio.Task[None]:
    task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(0))
    await task
    return task


def _targets(*names: str) -> dict[str, _PublishTarget]:
    return {
        name: _PublishTarget(name, i, f"/dev/video{i}", 1920, 1080, 30)
        for i, name in enumerate(names)
    }


def test_unreported_view_publishes_all(tmp_path: Path) -> None:
    service = _service(tmp_path)  # _active_view is None until the webapp reports
    by_name = _targets("cam_0", "cam_1", "cam_2")
    assert service._desired_cameras(by_name) == {"cam_0", "cam_1", "cam_2"}


def test_camera_setup_and_extrinsic_views_publish_all(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    service.set_active_view("cameras")
    assert service._desired_cameras(by_name) == {"cam_0", "cam_1"}
    service.set_active_view("extrinsic")
    assert service._desired_cameras(by_name) == {"cam_0", "cam_1"}


def test_intrinsic_view_publishes_only_the_active_camera(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1", "cam_2")
    service.set_active_view("intrinsic")
    service.set_active_intrinsic("cam_1")
    assert service._desired_cameras(by_name) == {"cam_1"}


def test_intrinsic_view_without_active_publishes_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    service.set_active_view("intrinsic")
    service.set_active_intrinsic(None)
    assert service._desired_cameras(by_name) == set()


def test_intrinsic_active_absent_from_targets_publishes_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    service.set_active_view("intrinsic")
    service.set_active_intrinsic("cam_9")  # not among the configured cameras
    assert service._desired_cameras(by_name) == set()


def test_passive_views_publish_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    for view in ("boards", "review", "export", "session", "load"):
        service.set_active_view(view)
        assert service._desired_cameras(by_name) == set(), view


def test_leaving_a_recording_camera_finalises_the_recorder(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        recorder = _FakeRecorder()
        service._recorder = recorder  # type: ignore[assignment]
        service._recording_camera = "cam_0"
        publisher = _FakePublisher()
        task = await _done_task()
        await service._stop_capture(publisher, "cam_0", task)  # type: ignore[arg-type]
        assert recorder.closed is True
        assert service._recorder is None
        assert service._recording_camera is None
        assert "cam_0" in publisher.muted

    asyncio.run(scenario())


def test_leaving_a_non_recording_camera_keeps_the_recorder(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        recorder = _FakeRecorder()
        service._recorder = recorder  # type: ignore[assignment]
        service._recording_camera = "cam_0"
        task = await _done_task()
        await service._stop_capture(_FakePublisher(), "cam_1", task)  # type: ignore[arg-type]
        assert recorder.closed is False
        assert service._recorder is recorder
        assert service._recording_camera == "cam_0"

    asyncio.run(scenario())


def test_capture_loop_releases_the_camera_in_its_finally(tmp_path: Path) -> None:
    # The LOOP owns the device (real-rig wedge fix): cancelling the loop must
    # release the camera exactly once, from inside the loop's own finally.
    async def scenario() -> None:
        service = _service(tmp_path)
        camera = _FakeCamera()

        async def hang(*_args: object) -> None:
            await asyncio.sleep(3600)

        service._capture_frames = hang  # type: ignore[method-assign]
        target = _PublishTarget("cam_0", 0, "/dev/video0", 1920, 1080, 30)
        task = asyncio.create_task(
            service._capture_loop(asyncio.get_running_loop(), None, target, camera)  # type: ignore[arg-type]
        )
        await asyncio.sleep(0.01)
        assert camera.released is False  # loop running: device held
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert camera.released is True  # finally ran after the loop ended

    asyncio.run(scenario())


def test_lifecycle_lock_serializes_concurrent_refreshes(tmp_path: Path) -> None:
    # Two quick drag-reorders fire two concurrent refresh() calls: they must
    # queue (never two live sessions, never a re-cancel during cleanup) and
    # leave exactly ONE running session at the end (real-rig orphan-session fix).
    async def scenario() -> None:
        service = _service(tmp_path)
        alive = 0
        peak = 0

        async def fake_session() -> None:
            nonlocal alive, peak
            alive += 1
            peak = max(peak, alive)
            try:
                await asyncio.sleep(3600)
            finally:
                alive -= 1

        service._publish_session = fake_session  # type: ignore[method-assign]
        await service.start()
        await asyncio.sleep(0.01)
        await asyncio.gather(service.refresh(), service.refresh(), service.refresh())
        await asyncio.sleep(0.01)
        assert peak == 1  # sessions never overlapped
        assert alive == 1  # exactly one survivor
        await service.stop()
        assert alive == 0

    asyncio.run(scenario())
