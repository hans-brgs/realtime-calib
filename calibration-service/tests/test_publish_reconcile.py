"""On-demand capture reconciliation: the view -> live-camera-set mapping (ADR-0021)."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from calibration_service.config import LiveKitConfig
from calibration_service.models.session import CameraConfig, SessionMode
from calibration_service.session.manager import SessionManager
from calibration_service.transport.camera_publish_service import (
    CameraPublishService,
    _PublishTarget,
)


def _service(tmp_path: Path) -> CameraPublishService:
    return CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))


def test_load_from_files_session_resolves_no_targets(tmp_path: Path) -> None:
    # Imported session (ADR-0031): capture is neutralised — even with cameras
    # configured, the publisher must resolve ZERO targets (no V4L2, no tracks).
    manager = SessionManager(tmp_path, "imported")
    session = manager.current()
    session.cameras = [
        CameraConfig(
            index=0,
            name="cam_0",
            prefix="cam",
            device_path="import:cam_0.mkv",
            device_node="",
            width=64,
            height=48,
            resize_factor=1.0,
            fps=30,
        )
    ]
    session.mode = SessionMode.LOAD_FROM_FILES
    service = CameraPublishService(LiveKitConfig(), manager)

    assert service._resolve_targets() == []

    # Sanity: the SAME session in realtime mode would publish — the guard is
    # what empties the set, not the empty device node.
    session.mode = SessionMode.NEW_REALTIME
    assert len(service._resolve_targets()) == 1


class _FakePublisher:
    def __init__(self) -> None:
        self.muted: list[str] = []

    def mute(self, name: str) -> None:
        self.muted.append(name)


class _FakeTrackPublisher:
    """Records track publishes + mutes for the track-set reconcile (ADR-0029)."""

    def __init__(self) -> None:
        self.published: list[str] = []
        self.muted: list[str] = []

    async def publish_camera_track(self, name: str, width: int, height: int, fps: int) -> None:
        self.published.append(name)

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
        # The fake was force-assigned above; identity is the point of the check.
        assert service._recorder is recorder  # type: ignore[comparison-overlap]
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

        service._capture_frames = hang  # type: ignore[method-assign, assignment]
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


def test_refresh_signals_the_session_without_reconnecting(tmp_path: Path) -> None:
    # ADR-0029: refresh() reconciles IN PLACE (signals the reconcile event); it must NOT
    # tear down + restart the publish session — that disconnect/reconnect crashes the
    # LiveKit FFI. Concurrent refreshes on a running session leave it started exactly
    # once (no reconnect), each just waking the reconcile loop.
    async def scenario() -> None:
        service = _service(tmp_path)
        starts = 0
        reconciles = 0

        async def fake_session() -> None:
            nonlocal starts, reconciles
            starts += 1
            while True:  # emulate the persistent reconcile loop
                service._reconcile.clear()
                await service._reconcile.wait()
                reconciles += 1

        service._publish_session = fake_session  # type: ignore[method-assign]
        await service.start()
        await asyncio.sleep(0.01)
        await asyncio.gather(service.refresh(), service.refresh(), service.refresh())
        await asyncio.sleep(0.01)
        assert starts == 1  # signalled, never restarted -> no reconnect
        assert reconciles >= 1  # refresh woke the reconcile loop
        await service.stop()

    asyncio.run(scenario())


def test_reconfigure_reconciles_tracks_in_place(tmp_path: Path) -> None:
    # ADR-0029: on reconfiguration the track set is reconciled in place — a new camera's
    # track is published (muted), a removed camera's track is muted (never unpublished,
    # #449). No reconnect (connect is never involved here).
    async def scenario() -> None:
        service = _service(tmp_path)
        publisher = _FakeTrackPublisher()
        published: dict[str, tuple[int, int]] = {}
        by_name: dict[str, _PublishTarget] = {}

        # Initial config: cam_0, cam_1 -> both tracks published.
        needs = await service._reconcile_tracks(
            publisher,  # type: ignore[arg-type]
            list(_targets("cam_0", "cam_1").values()),
            published,
            by_name,
        )
        assert needs is False
        assert publisher.published == ["cam_0", "cam_1"]
        assert set(by_name) == {"cam_0", "cam_1"}

        # Reconfigure: cam_0 kept, cam_1 removed, cam_2 added.
        reconf = [
            _PublishTarget("cam_0", 0, "/dev/video0", 1920, 1080, 30),
            _PublishTarget("cam_2", 2, "/dev/video2", 1920, 1080, 30),
        ]
        needs = await service._reconcile_tracks(publisher, reconf, published, by_name)  # type: ignore[arg-type]
        assert needs is False
        assert "cam_2" in publisher.published  # new camera -> track published
        assert publisher.muted == ["cam_1"]  # removed camera -> muted, not unpublished
        assert set(by_name) == {"cam_0", "cam_2"}  # by_name = current config only

    asyncio.run(scenario())
