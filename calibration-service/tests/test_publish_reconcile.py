"""On-demand capture reconciliation: the view -> live-camera-set mapping (ADR-0021)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest

from calibration_service.capture.camera import CameraOpenError
from calibration_service.config import LiveKitConfig
from calibration_service.models.session import CameraConfig, SessionMode
from calibration_service.session.manager import SessionManager
from calibration_service.transport import camera_publish_service
from calibration_service.transport.camera_publish_service import (
    CameraPublishService,
    _PublishTarget,
)


def _service(tmp_path: Path) -> CameraPublishService:
    return CameraPublishService(LiveKitConfig(), SessionManager(tmp_path, "default"))


def test_load_from_files_session_resolves_no_targets(tmp_path: Path) -> None:
    # Imported session (ADR-0035): capture is neutralised — even with cameras
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


def _running_task() -> asyncio.Task[None]:
    """A capture loop still running (asyncio.run cancels it at teardown)."""
    return asyncio.create_task(asyncio.Event().wait())


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


async def _open_set_harness(
    service: CameraPublishService,
) -> tuple[list[_PublishTarget], list[str]]:
    """Stub the device-level open/close so the reconcile can be driven without V4L2."""
    opened: list[_PublishTarget] = []
    stopped: list[str] = []

    async def fake_start(
        _loop: object, _executor: object, _publisher: object, target: _PublishTarget
    ) -> tuple[_FakeCamera, asyncio.Task[None]]:
        opened.append(target)
        # A RUNNING loop: a done task now means "the camera ended on its own" (#46).
        return _FakeCamera(), _running_task()

    async def fake_stop(_publisher: object, name: str, _task: object) -> None:
        stopped.append(name)

    service._start_capture = fake_start  # type: ignore[method-assign, assignment]
    service._stop_capture = fake_stop  # type: ignore[method-assign, assignment]
    return opened, stopped


def test_reorder_reopens_each_camera_on_its_new_device(tmp_path: Path) -> None:
    # Reordering the array rebinds cam_<i> to a DIFFERENT device_node while the name set
    # and the preview sizes stay identical — so the track reconcile publishes nothing and
    # the desired set is unchanged. The open set must still notice, or cam_0 keeps
    # streaming the device it was first opened on and the reorder looks ignored (it only
    # took effect after a manual reload happened to cycle the view).
    async def scenario() -> None:
        service = _service(tmp_path)
        opened, stopped = await _open_set_harness(service)
        loop = asyncio.get_running_loop()
        open_cams: dict[str, tuple[object, asyncio.Task[None], _PublishTarget]] = {}

        by_name = _targets("cam_0", "cam_1")  # cam_0 -> video0, cam_1 -> video1
        await service._reconcile_open_set(loop, None, None, by_name, open_cams)  # type: ignore[arg-type]
        # Sets, not lists: the desired set is a `set`, so the open ORDER is not guaranteed.
        assert {t.device_node for t in opened} == {"/dev/video0", "/dev/video1"}
        assert stopped == []

        # Idempotence: an unchanged config must not churn the devices.
        await service._reconcile_open_set(loop, None, None, by_name, open_cams)  # type: ignore[arg-type]
        assert len(opened) == 2
        assert stopped == []

        # Reorder: the two devices swap names. Same names, same sizes, same desired set.
        swapped = {
            "cam_0": _PublishTarget("cam_0", 0, "/dev/video1", 1920, 1080, 30),
            "cam_1": _PublishTarget("cam_1", 1, "/dev/video0", 1920, 1080, 30),
        }
        await service._reconcile_open_set(loop, None, None, swapped, open_cams)  # type: ignore[arg-type]
        assert sorted(stopped) == ["cam_0", "cam_1"]  # both closed...
        assert {t.device_node for t in opened[2:]} == {"/dev/video1", "/dev/video0"}  # ...reopened
        # What actually matters: each NAME is now bound to the other device.
        assert {name: entry[2].device_node for name, entry in open_cams.items()} == {
            "cam_0": "/dev/video1",
            "cam_1": "/dev/video0",
        }

    asyncio.run(scenario())


def test_fps_change_reopens_the_camera(tmp_path: Path) -> None:
    # Same root cause as the reorder: an fps change leaves the preview size untouched, so
    # the track reconcile does nothing — but the open V4L2 handle carries the old rate.
    async def scenario() -> None:
        service = _service(tmp_path)
        opened, stopped = await _open_set_harness(service)
        loop = asyncio.get_running_loop()
        open_cams: dict[str, tuple[object, asyncio.Task[None], _PublishTarget]] = {}

        await service._reconcile_open_set(loop, None, None, _targets("cam_0"), open_cams)  # type: ignore[arg-type]
        assert [t.fps for t in opened] == [30]

        retuned = {"cam_0": _PublishTarget("cam_0", 0, "/dev/video0", 1920, 1080, 15)}
        await service._reconcile_open_set(loop, None, None, retuned, open_cams)  # type: ignore[arg-type]
        assert stopped == ["cam_0"]
        assert [t.fps for t in opened] == [30, 15]

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


# --- Capture health (#46): failures reach the registry, retries back off --------


class _FakeLivePublisher:
    """The publisher surface _start_capture touches, recording camera_state sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_data(self, payload: str, topic: str) -> None:
        self.sent.append(json.loads(payload))

    def push(self, name: str, image: object) -> None:
        pass

    def unmute(self, name: str) -> None:
        pass

    def mute(self, name: str) -> None:
        pass


def test_a_camera_that_cannot_open_is_reported_and_backs_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    def failing_open(device_node: str, *_args: object, **_kwargs: object) -> None:
        attempts.append(device_node)
        raise CameraOpenError(f"cannot open camera device {device_node!r}")

    monkeypatch.setattr(camera_publish_service, "open_camera", failing_open)

    async def scenario() -> None:
        service = _service(tmp_path)
        publisher = _FakeLivePublisher()
        loop = asyncio.get_running_loop()
        open_cams: dict[str, tuple[object, asyncio.Task[None], _PublishTarget]] = {}
        by_name = {"cam_0": _PublishTarget("cam_0", 0, "/dev/video4", 1920, 1080, 30)}

        await service._reconcile_open_set(loop, None, publisher, by_name, open_cams)  # type: ignore[arg-type]
        assert attempts == ["/dev/video4"]
        assert open_cams == {}
        # OPENING went out before the attempt, so the webapp is never left stale.
        assert publisher.sent[0]["cameras"]["cam_0"]["state"] == "opening"  # type: ignore[index]
        state = service._health.snapshot(loop.time())["cam_0"]
        assert state["state"] == "error"
        assert state["reason"] == "cannot open /dev/video4"

        # The next tick lands inside the 1 s backoff: no new attempt — the old
        # behaviour hammered the device (and the log) every second, forever.
        await service._reconcile_open_set(loop, None, publisher, by_name, open_cams)  # type: ignore[arg-type]
        assert attempts == ["/dev/video4"]

    asyncio.run(scenario())


def test_a_camera_lost_mid_capture_is_reported_and_not_reopened_at_once(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        opened, stopped = await _open_set_harness(service)
        loop = asyncio.get_running_loop()
        open_cams: dict[str, tuple[object, asyncio.Task[None], _PublishTarget]] = {}
        by_name = _targets("cam_0")
        await service._reconcile_open_set(loop, None, None, by_name, open_cams)  # type: ignore[arg-type]
        assert len(opened) == 1

        # The capture loop gave up on a dead device.
        async def lost() -> None:
            raise camera_publish_service._CameraLostError("no frame for 3 s")

        lost_task = asyncio.create_task(lost())
        await asyncio.sleep(0)
        camera, _task, target = open_cams["cam_0"]
        open_cams["cam_0"] = (camera, lost_task, target)

        await service._reconcile_open_set(loop, None, None, by_name, open_cams)  # type: ignore[arg-type]
        assert stopped == ["cam_0"]  # cleaned up (recording finalised, track muted)
        assert len(opened) == 1  # not reopened inside the backoff
        state = service._health.snapshot(loop.time())["cam_0"]
        assert (state["state"], state["reason"]) == ("error", "no frame for 3 s")

    asyncio.run(scenario())


def test_a_crashed_capture_loop_is_reported_as_a_camera_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path)
        _opened, _stopped = await _open_set_harness(service)
        loop = asyncio.get_running_loop()
        open_cams: dict[str, tuple[object, asyncio.Task[None], _PublishTarget]] = {}
        by_name = _targets("cam_0")
        await service._reconcile_open_set(loop, None, None, by_name, open_cams)  # type: ignore[arg-type]

        async def crash() -> None:
            raise RuntimeError("boom")

        crashed = asyncio.create_task(crash())
        await asyncio.sleep(0)
        camera, _task, target = open_cams["cam_0"]
        open_cams["cam_0"] = (camera, crashed, target)

        await service._reconcile_open_set(loop, None, None, by_name, open_cams)  # type: ignore[arg-type]
        state = service._health.snapshot(loop.time())["cam_0"]
        assert state["state"] == "error"
        assert state["reason"] == "capture stopped unexpectedly (RuntimeError)"

    asyncio.run(scenario())


class _DeadCamera(_FakeCamera):
    def grab(self) -> bool:
        return False


def test_capture_loop_declares_a_camera_lost_after_sustained_grab_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(camera_publish_service, "_CAMERA_LOST_AFTER_S", 0.05)

    class _Connected:
        def is_disconnected(self) -> bool:
            return False

    async def scenario() -> None:
        service = _service(tmp_path)
        target = _PublishTarget("cam_0", 0, "/dev/video0", 1920, 1080, 30)
        with pytest.raises(camera_publish_service._CameraLostError, match="no frame"):
            await asyncio.wait_for(
                service._capture_frames(
                    asyncio.get_running_loop(),
                    _Connected(),  # type: ignore[arg-type]
                    target,
                    _DeadCamera(),  # type: ignore[arg-type]
                ),
                timeout=2.0,
            )

    asyncio.run(scenario())
