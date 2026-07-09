"""Publishes cameras to LiveKit, driven by the session config (Phase 1).

One participant publishes every camera as a separate track (ADR-0018): a single
LiveKit connection carries N tracks, with one capture task per camera (capture is
blocking → executor) pushing into its track's source. Before configuration the
*detected* cameras are published (so the operator can identify them); after
configuration the *configured* cameras (names, order, native resolution).
``refresh()`` re-publishes from the current session when the config changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.capture.camera import CameraCapture, CameraOpenError, open_camera
from calibration_service.capture.enumeration import enumerate_cameras
from calibration_service.config import LiveKitConfig
from calibration_service.detection import BoardDetection, BoardDetector
from calibration_service.models.frame import Frame
from calibration_service.overlays import draw_overlay
from calibration_service.recording import (
    CameraSpec,
    ExtrinsicRecorder,
    PreviewJobs,
    VideoRecorder,
)
from calibration_service.session.manager import SessionManager
from calibration_service.synchronization import CovisibilityGraph, FrameSynchronizer
from calibration_service.telemetry import (
    TELEMETRY_TOPIC,
    coverage_metrics_payload,
    covisibility_payload,
)
from calibration_service.transport.livekit_publisher import LiveKitPublisher, mint_publish_token

logger = logging.getLogger(__name__)


def _process_frame(
    detector: BoardDetector, image: NDArray[np.uint8], preview_size: tuple[int, int]
) -> tuple[NDArray[np.uint8], BoardDetection]:
    """Detect the board (native res) + burn the overlay in, downscaled to the preview.

    CPU-bound; meant for an executor. Detection uses the full-resolution frame; the
    returned preview is the downscaled burn-in for LiveKit.
    """
    detection = detector.detect(image)
    return _draw_preview(image, detection, preview_size), detection


def _draw_preview(
    image: NDArray[np.uint8],
    detection: BoardDetection | None,
    preview_size: tuple[int, int],
) -> NDArray[np.uint8]:
    """Burn the (last known) detection overlay in and downscale — no re-detection.

    Drawn on every published frame so the board overlay stays steady at the preview
    rate instead of flickering at the (slower) detection rate. ``None`` -> raw preview.
    """
    if detection is None:
        return _downscale(image, preview_size)
    return _downscale(draw_overlay(image, detection, resize_factor=1.0), preview_size)

_EMPTY_READ_BACKOFF_S = 0.005
_FIRST_FRAME_ATTEMPTS = 60
_FIRST_FRAME_BACKOFF_S = 0.1
# Backoff before reconnecting (LiveKit dropped / cameras unavailable).
_RECONNECT_BACKOFF_S = 2.0
# Stagger camera opens to ease simultaneous USB bandwidth negotiation (4 cameras).
_OPEN_STAGGER_S = 0.5
# Preview fps before configuration: a safe default (avoids over-driving a USB 2.0
# camera at e.g. 60 fps). After configuration the operator-chosen fps is used.
_DEFAULT_FPS = 30
# Single publisher participant; cameras are distinguished by their track name.
_PARTICIPANT_IDENTITY = "service"
# Telemetry cadence on the data channel (coverage metrics ~10 Hz, not per frame).
_TELEMETRY_PERIOD_S = 0.1
# Board detection + burn-in run only on the single active intrinsic camera now
# (ADR-0021), so there is CPU headroom to detect at the preview push rate (~30 Hz)
# for a tightly-tracking overlay. This is the live-feedback rate ONLY; the intrinsic
# *compute* re-detects on the recorded video (its own frame stride), so it is
# unaffected by this value.
_DETECT_PERIOD_S = 1 / 30
# Recording rate: ~30 Hz of native frames is ample to select keyframes from, and
# caps the encode load. Writes run off the event loop. The recorded file declares
# min(capture fps, _RECORD_FPS) so its playback speed matches the write cadence.
_RECORD_FPS = 30
_RECORD_PERIOD_S = 1 / _RECORD_FPS
# Preview published to LiveKit is downscaled + rate-capped (ADR-0015): capture stays
# native (for recording/detection), but publishing 4x1080p@60 overloads the CPU VP8
# encoder. Downscaling the preview cuts encode ~4x and the fps cap ~2x.
_PREVIEW_MAX_WIDTH = 960
_PREVIEW_FPS = 30
_PUSH_PERIOD_S = 1 / _PREVIEW_FPS
# Dedicated capture thread pool (ADR-0021): isolate the blocking cv2 reads/decodes/
# writes from the default executor (shared with the intrinsic compute + sync HTTP
# routes). Sized for a handful of cameras each parking a thread in a blocking grab()
# plus brief retrieve/detect/write bursts; threads parked in grab() cost ~no CPU.
_CAPTURE_THREADS = 16
# Re-check the desired camera set at least this often (also woken immediately on a
# view / active-camera change), and use it to notice a dropped room.
_RECONCILE_TICK_S = 1.0
# Wizard views (webapp) whose screen needs every camera live at once (ADR-0021).
_ALL_CAMERA_VIEWS = frozenset({"cameras", "extrinsic"})
_INTRINSIC_VIEW = "intrinsic"
_EXTRINSIC_VIEW = "extrinsic"
# Extrinsic sweep: detection runs on EVERY camera, so a lower rate than the
# single-camera intrinsic 30 Hz (4x native detections ~4 cores). Detection fires on
# a shared wall-clock grid (int(now/period)) so all cameras detect the same instant
# — their frame timestamps then differ by at most ~1 frame interval, which the sync
# window accommodates. Live-feedback only; the compute re-detects the recordings.
# Provisional 15 Hz; goal is to match the intrinsic 30 Hz once end-to-end CPU load
# is validated on the real 4-camera rig (operator request).
_EXTRINSIC_DETECT_PERIOD_S = 1 / 15
# Co-visibility matrix push rate (small payload; the gauges don't need more).
_COVISIBILITY_PERIOD_S = 0.33


def _preview_size(width: int, height: int) -> tuple[int, int]:
    """Downscaled, even-dimensioned preview size (<= _PREVIEW_MAX_WIDTH wide)."""
    scale = min(1.0, _PREVIEW_MAX_WIDTH / width)
    return (round(width * scale) & ~1, round(height * scale) & ~1)


def _downscale(image: NDArray[np.uint8], size: tuple[int, int]) -> NDArray[np.uint8]:
    if (image.shape[1], image.shape[0]) == size:
        return image
    return cast("NDArray[np.uint8]", cv2.resize(image, size, interpolation=cv2.INTER_AREA))


@dataclass(frozen=True)
class _PublishTarget:
    """What to publish for one camera: track name + capture parameters."""

    name: str
    index: int
    device_node: str
    width: int
    height: int
    fps: int


class CameraPublishService:
    """Publishes all cameras as one participant with N tracks; re-launchable on config change."""

    def __init__(
        self,
        config: LiveKitConfig,
        session_manager: SessionManager,
        preview_jobs: PreviewJobs | None = None,
    ) -> None:
        self._config = config
        self._sessions = session_manager
        self._previews = preview_jobs  # background H.264 preview transcodes (ADR-0027)
        self._task: asyncio.Task[None] | None = None
        self._capture_executor: ThreadPoolExecutor | None = None
        # Serializes start/stop/refresh: concurrent refreshes (two quick drag
        # reorders) must queue, not interleave — an interleaved stop re-cancelled
        # the previous session DURING its cleanup finally, truncating it (cameras
        # never released -> V4L2 wedge) and leaving an orphan session behind.
        self._lifecycle = asyncio.Lock()
        # On-demand capture (ADR-0021): only the cameras the current view needs are
        # opened + published (unmuted). `_active_view` is the operator's wizard view
        # (reported by the webapp); None = not reported yet -> publish all (safe
        # default until the webapp is wired). `_reconcile` wakes the reconcile loop.
        self._active_view: str | None = None
        self._reconcile = asyncio.Event()
        # Set by refresh() (config/session change) so the persistent session re-resolves
        # targets + reconciles the TRACK set (ADR-0029). A bare view change does NOT set
        # it (no re-enumerate): only the open-set reconciles.
        self._targets_dirty = False
        # Track name currently in intrinsic capture: only that camera runs detection +
        # burn-in + telemetry; in the intrinsic view it is the *only* camera live.
        self._active_intrinsic: str | None = None
        # Recording the raw sweep of the active camera to disk (ADR-0019), if any.
        # Writes run in the executor; the lock serialises write vs close (stop waits
        # for an in-flight write before finalising the file). `_recording_camera` is
        # the track being recorded, so the reconciler can finalise the sweep if that
        # camera leaves the live set (navigation / switch) mid-recording (ADR-0021).
        self._recorder: VideoRecorder | None = None
        self._recording_camera: str | None = None
        self._recorder_lock = asyncio.Lock()
        # Synchronized extrinsic sweep (ADR-0007/0023): every camera records to its
        # own video + timestamp sidecar; detections feed the synchronizer + the
        # co-visibility graph for the live gauges. Per-camera write locks let the N
        # loops write concurrently (different files) while stop still serialises
        # close against in-flight writes camera by camera.
        self._extrinsic: ExtrinsicRecorder | None = None
        self._extrinsic_locks: dict[str, asyncio.Lock] = {}
        self._extrinsic_stop_lock = asyncio.Lock()
        self._ext_sync: FrameSynchronizer[bool] | None = None
        self._ext_graph: CovisibilityGraph | None = None
        self._last_covisibility = 0.0

    def set_active_view(self, view: str | None) -> None:
        """Report the operator's current wizard view; drives the live set (ADR-0021)."""
        logger.info("active view -> %s", view)
        self._active_view = view
        self._reconcile.set()

    def set_active_intrinsic(self, name: str | None) -> None:
        """Select the camera whose feed gets board detection/overlay/telemetry.

        In the intrinsic view this is also the *only* camera kept live, so a change
        reconciles the open set (close the previous camera, open the new one).
        """
        logger.info("active intrinsic camera -> %s", name)
        self._active_intrinsic = name
        self._reconcile.set()

    async def start_intrinsic_recording(self, camera_name: str) -> None:
        """Make ``camera_name`` active and record its raw sweep to the session folder."""
        await self.stop_intrinsic_recording()
        self.set_active_intrinsic(camera_name)
        camera = next((c for c in self._sessions.current().cameras if c.name == camera_name), None)
        if camera is None:
            raise ValueError(f"unknown camera {camera_name!r}")
        path = self._sessions.intrinsic_video_path(camera_name)
        if self._previews is not None:
            self._previews.invalidate(path)  # overwrite in progress: stale mp4 out
        # Written frames are throttled to _RECORD_FPS; declare the true cadence so the
        # replayed mkv plays at real time (not Nx fast) whatever the capture fps.
        record_fps = min(camera.fps, _RECORD_FPS) if camera.fps else _RECORD_FPS
        async with self._recorder_lock:
            self._recorder = VideoRecorder(path, camera.width, camera.height, record_fps)
            self._recording_camera = camera_name
        logger.info("recording intrinsic sweep of %s -> %s", camera_name, path)

    async def stop_intrinsic_recording(self) -> int:
        """Finalise the current recording (if any) and return the frame count."""
        async with self._recorder_lock:
            recorder = self._recorder
            camera = self._recording_camera
            self._recorder = None
            self._recording_camera = None
            if recorder is None:
                return 0
            recorder.close()
            logger.info("stopped recording (%d frames)", recorder.frames)
        if self._previews is not None and camera is not None and recorder.frames > 0:
            # Kick the preview transcode NOW (ADR-0027): usually done before the
            # operator reaches Prepare; the webapp shows a progress popup meanwhile.
            self._previews.ensure(self._sessions.intrinsic_video_path(camera))
        return recorder.frames

    async def start_extrinsic_recording(self) -> None:
        """Begin the synchronized multi-camera sweep (ADR-0007, [[calibration-recording]]).

        Every configured camera records to ``extrinsic/<cam>.mkv`` + a timestamp
        sidecar; per-camera detections feed the synchronizer + co-visibility graph.
        """
        await self.stop_extrinsic_recording()
        await self.stop_intrinsic_recording()
        self.set_active_intrinsic(None)
        cameras = self._sessions.current().cameras
        if len(cameras) < 2:
            raise ValueError("extrinsic capture needs at least 2 configured cameras")
        specs = [
            CameraSpec(c.name, c.width, c.height, min(c.fps, _RECORD_FPS) if c.fps else _RECORD_FPS)
            for c in cameras
        ]
        names = [c.name for c in cameras]
        if self._previews is not None:
            for name in names:  # overwrite in progress: stale previews out
                self._previews.invalidate(self._sessions.extrinsic_dir() / f"{name}.mkv")
        # Sync window: detections fire on a shared wall-clock grid, so members of one
        # instant differ by at most ~one camera frame interval (ADR-0007: < 1/fps).
        window = 1.2 * max(1.0 / c.fps if c.fps else 1.0 / _DEFAULT_FPS for c in cameras)
        async with self._extrinsic_stop_lock:
            self._extrinsic_locks = {name: asyncio.Lock() for name in names}
            self._ext_sync = FrameSynchronizer(names, window)
            self._ext_graph = CovisibilityGraph(names)
            self._last_covisibility = 0.0
            self._extrinsic = ExtrinsicRecorder(self._sessions.extrinsic_dir(), specs)
        self._reconcile.set()  # keep/reopen every camera while the sweep runs
        logger.info("recording extrinsic sweep of %d cameras", len(specs))

    async def stop_extrinsic_recording(self) -> dict[str, int]:
        """Finalise the synchronized sweep; return per-camera frame counts."""
        async with self._extrinsic_stop_lock:
            recorder = self._extrinsic
            if recorder is None:
                return {}
            self._extrinsic = None  # loops stop scheduling new writes
            # Wait out in-flight writes: once each per-camera lock is acquired, no
            # write scheduled against the old recorder can still be running.
            for lock in self._extrinsic_locks.values():
                async with lock:
                    pass
            self._ext_sync = None
            self._ext_graph = None
            loop = asyncio.get_running_loop()
            counts = await loop.run_in_executor(None, recorder.close)
        self._reconcile.set()
        if self._previews is not None:
            for name, frames in counts.items():
                if frames > 0:  # kick the preview transcodes NOW (ADR-0027)
                    self._previews.ensure(self._sessions.extrinsic_dir() / f"{name}.mkv")
        return counts

    def _feed_extrinsic(
        self, camera: str, timestamp: float, detection: BoardDetection, now: float
    ) -> dict[str, object] | None:
        """Feed one detection to the synchronizer; return a co-visibility payload when due.

        Called from the capture coroutines only (single-threaded event loop, no lock).
        """
        sync, graph = self._ext_sync, self._ext_graph
        if sync is None or graph is None:
            return None
        sync.add(camera, timestamp, detection.found)
        while (group := sync.try_emit()) is not None:
            graph.record({name: frame.payload for name, frame in group.frames.items()})
        if now - self._last_covisibility >= _COVISIBILITY_PERIOD_S:
            self._last_covisibility = now
            return covisibility_payload(graph)
        return None

    def _build_detector(self, *, extrinsic: bool = False) -> BoardDetector | None:
        session = self._sessions.current_or_none()
        if session is None:
            return None
        board = session.effective_extrinsic_board() if extrinsic else session.intrinsic_board
        return BoardDetector(board) if board is not None else None

    async def start(self) -> None:
        async with self._lifecycle:
            self._start_locked()

    async def refresh(self) -> None:
        """Reconcile the publisher against the current session/config (ADR-0029).

        NO teardown/reconnect: it signals the persistent publish session to re-resolve
        targets and reconcile the track + open sets IN PLACE (a disconnect/reconnect
        crashes the LiveKit FFI). If no session is running yet (idle), it starts the run
        loop, which connects once a session has cameras. Serialized under the lifecycle
        lock so two concurrent idle-starts can't spawn overlapping sessions.
        """
        logger.info("refreshing camera publishers")
        async with self._lifecycle:
            self._targets_dirty = True
            if self._task is None:
                self._start_locked()
            else:
                self._reconcile.set()

    async def stop(self) -> None:
        async with self._lifecycle:
            await self._stop_locked()

    def _start_locked(self) -> None:
        if self._task is not None:
            return  # already running — refresh() is the resync path
        self._capture_executor = ThreadPoolExecutor(
            max_workers=_CAPTURE_THREADS, thread_name_prefix="capture"
        )
        self._task = asyncio.create_task(self._run(), name="camera-publish")

    async def _stop_locked(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        if self._capture_executor is not None:
            executor = self._capture_executor
            self._capture_executor = None
            # wait=True: no capture thread may still touch a V4L2 handle when the
            # next session reopens the devices (bounded by one in-flight grab).
            # Run OFF the event loop — a synchronous wait would freeze it.
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: executor.shutdown(wait=True, cancel_futures=True)
            )

    async def _run(self) -> None:
        """Session loop: hold one persistent publish session; when it returns (network
        drop or a deliberate session close/resize) wait — interruptibly — before
        retrying. A refresh() wakes the wait so a new session connects promptly (ADR-0029).
        """
        while True:
            self._reconcile.clear()
            try:
                await self._publish_session()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("publish session failed; reconnecting")
            # Interruptible backoff: a refresh (new/changed session) wakes us at once.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._reconcile.wait(), timeout=_RECONNECT_BACKOFF_S)

    def _resolve_targets(self) -> list[_PublishTarget]:
        session = self._sessions.current_or_none()
        if session is None:
            return []  # no active session (dashboard): idle, don't touch V4L2 (ADR-0028)
        if session.cameras:
            return [
                _PublishTarget(c.name, c.index, c.device_node, c.width, c.height, c.fps)
                for c in session.cameras
            ]
        # Not configured yet: publish detected cameras for identification.
        return [
            _PublishTarget(
                f"cam_{d.index}", d.index, d.device_node, d.width, d.height, _DEFAULT_FPS
            )
            for d in enumerate_cameras()
        ]

    async def _publish_session(self) -> None:
        """Hold one persistent connection, reconciling the published-track set and the
        open set in a loop (ADR-0029), until the room drops or the session closes.

        On-demand capture (ADR-0021): tracks are published up-front (muted) and opened
        on demand per view. On a config change the track set is reconciled IN PLACE — a
        new camera's track is published, a removed one muted (never unpublished, #449) —
        with NO reconnect; the disconnect/reconnect that crashes the LiveKit FFI happens
        only on a real network drop (``_run`` republishes all) or a deliberate session
        close/resize.
        """
        loop = asyncio.get_running_loop()
        executor = self._capture_executor
        if executor is None:
            return
        targets = await loop.run_in_executor(executor, self._resolve_targets)
        if not targets:
            # No active session (dashboard) is the normal idle state, not a fault — stay
            # disconnected until a session with cameras appears (refresh() wakes _run).
            if self._sessions.current_or_none() is None:
                logger.debug("no active session; publisher idle")
            else:
                logger.warning("no camera to publish")
            return
        # Published-track set for THIS connection (union of cameras seen, ADR-0029):
        # track name -> published preview size. `by_name` is the CURRENT config, rebuilt
        # each reconcile; a camera skipped for missing dimensions never enters it.
        published: dict[str, tuple[int, int]] = {}
        by_name: dict[str, _PublishTarget] = {}

        token = mint_publish_token(
            self._config, identity=_PARTICIPANT_IDENTITY, room=self._config.room_name
        )
        publisher = LiveKitPublisher()
        open_cams: dict[str, tuple[CameraCapture, asyncio.Task[None]]] = {}
        try:
            await publisher.connect(self._config.url, token)
            await publisher.await_connected()  # WebRTC handshake before media (#449)
            self._targets_dirty = True  # publish the initial track set on the first tick
            while not publisher.is_disconnected():
                self._reconcile.clear()
                if self._targets_dirty:
                    self._targets_dirty = False
                    targets = await loop.run_in_executor(executor, self._resolve_targets)
                    if not targets:
                        break  # session closed -> graceful disconnect (ADR-0029)
                    if await self._reconcile_tracks(publisher, targets, published, by_name):
                        break  # a track needs a new size -> reconnect to republish it
                await self._reconcile_open_set(loop, executor, publisher, by_name, open_cams)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._reconcile.wait(), timeout=_RECONCILE_TICK_S)
        finally:
            # A room drop mid-sweep must not leave N dangling video files open.
            await self.stop_extrinsic_recording()
            for name in list(open_cams):
                _camera, task = open_cams.pop(name)
                await self._stop_capture(publisher, name, task)
            await publisher.aclose()

    async def _reconcile_tracks(
        self,
        publisher: LiveKitPublisher,
        targets: list[_PublishTarget],
        published: dict[str, tuple[int, int]],
        by_name: dict[str, _PublishTarget],
    ) -> bool:
        """Reconcile the published-track set to the current config (ADR-0029).

        Publishes a muted track for each newly-configured camera, mutes the ones no
        longer configured (never ``unpublish`` — leak #449), and rebuilds ``by_name`` to
        the current config so the open-set reconcile never opens a removed camera.
        Returns True if a still-configured camera now needs a DIFFERENT preview size than
        published (a track can't be resized in place) — the caller reconnects to
        republish at the new size (rare: an aspect-ratio change).
        """
        by_name.clear()
        for target in targets:
            size = _preview_size(target.width, target.height) if target.width else None
            if size is None:
                logger.warning("camera %s has no dimensions; not published", target.name)
                continue
            previous = published.get(target.name)
            if previous is None:
                await publisher.publish_camera_track(target.name, size[0], size[1], _PREVIEW_FPS)
                published[target.name] = size
            elif previous != size:
                logger.info(
                    "camera %s preview size %s -> %s; reconnecting to resize",
                    target.name,
                    previous,
                    size,
                )
                return True
            by_name[target.name] = target
        for name in published:
            if name not in by_name:
                publisher.mute(name)
        return False

    def _desired_cameras(self, by_name: dict[str, _PublishTarget]) -> set[str]:
        """The cameras that should be live for the current view (ADR-0021)."""
        if self._extrinsic is not None:
            # A synchronized sweep in progress needs every camera regardless of a
            # view flap (the set is what we WANT live — ADR-0021 semantics).
            return set(by_name)
        view = self._active_view
        if view is None or view in _ALL_CAMERA_VIEWS:
            return set(by_name)  # None = not reported yet -> publish all (safe default)
        if view == _INTRINSIC_VIEW:
            return {self._active_intrinsic} if self._active_intrinsic in by_name else set()
        return set()  # boards / review / export / entry -> no live camera

    async def _reconcile_open_set(
        self,
        loop: asyncio.AbstractEventLoop,
        executor: ThreadPoolExecutor,
        publisher: LiveKitPublisher,
        by_name: dict[str, _PublishTarget],
        open_cams: dict[str, tuple[CameraCapture, asyncio.Task[None]]],
    ) -> None:
        """Make the set of open cameras match the desired set: close leavers, open joiners."""
        desired = self._desired_cameras(by_name)
        for name in list(open_cams):
            if name not in desired:
                _camera, task = open_cams.pop(name)
                await self._stop_capture(publisher, name, task)
        joiners = [name for name in desired if name not in open_cams]
        for i, name in enumerate(joiners):
            opened = await self._start_capture(loop, executor, publisher, by_name[name])
            if opened is not None:
                open_cams[name] = opened
            if i + 1 < len(joiners):
                await asyncio.sleep(_OPEN_STAGGER_S)  # stagger USB negotiation

    async def _start_capture(
        self,
        loop: asyncio.AbstractEventLoop,
        executor: ThreadPoolExecutor,
        publisher: LiveKitPublisher,
        target: _PublishTarget,
    ) -> tuple[CameraCapture, asyncio.Task[None]] | None:
        """Open a camera, push its first frame, unmute its track and start its loop."""
        try:
            camera = open_camera(
                target.device_node,
                target.index,
                width=target.width or None,
                height=target.height or None,
                fps=target.fps or None,
            )
        except CameraOpenError:
            logger.exception("cannot open camera %s; skipping", target.device_node)
            return None
        first = await self._read_first_frame(loop, executor, camera)
        if first is None:
            logger.warning("camera %s produced no frame", target.name)
            camera.release()
            return None
        size = _preview_size(first.image.shape[1], first.image.shape[0])
        publisher.push(target.name, _downscale(first.image, size))
        publisher.unmute(target.name)  # first frame ready before unmuting (#449)
        task = asyncio.create_task(
            self._capture_loop(loop, publisher, target, camera),
            name=f"capture-{target.name}",
        )
        logger.info("camera %s live (opened + unmuted)", target.name)
        return camera, task

    async def _stop_capture(
        self,
        publisher: LiveKitPublisher,
        name: str,
        task: asyncio.Task[None],
    ) -> None:
        """Cancel a camera's loop and mute its track (ADR-0021).

        The loop OWNS the device and releases it in its own finally — awaiting the
        cancelled task here therefore returns only once the camera is closed.
        """
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # If this camera was being recorded (operator left the intrinsic view or
        # switched camera mid-sweep), finalise the file now rather than leaving it open.
        if name == self._recording_camera:
            await self.stop_intrinsic_recording()
        publisher.mute(name)  # keep the track (unpublish leaks, #449); just stop media
        logger.info("camera %s muted", name)

    async def _capture_loop(
        self,
        loop: asyncio.AbstractEventLoop,
        publisher: LiveKitPublisher,
        target: _PublishTarget,
        camera: CameraCapture,
    ) -> None:
        """Run one camera's frame loop; the LOOP owns the device.

        Releasing in our finally runs strictly after the in-flight executor call
        returned (a running executor future cannot be cancelled), so release never
        races a live V4L2 grab — a concurrent release wedged /dev/videoX on the
        real rig (endless select() timeouts until service restart).
        """
        try:
            await self._capture_frames(loop, publisher, target, camera)
        finally:
            camera.release()
            logger.info("camera %s released", target.name)

    async def _capture_frames(
        self,
        loop: asyncio.AbstractEventLoop,
        publisher: LiveKitPublisher,
        target: _PublishTarget,
        camera: CameraCapture,
    ) -> None:
        """Read one camera and push every frame to its track until the room disconnects.

        The pipeline is paced to the configured capture fps: every iteration cheaply
        ``grab()``s a frame (blocking, so the loop runs at the camera's native rate),
        but only ``retrieve()``s (decodes) one when the target interval has elapsed —
        honouring the chosen fps even if the driver ignores ``CAP_PROP_FPS``, and
        skipping the JPEG decode of frames we would drop. The preview published to
        LiveKit is then downscaled + rate-capped (ADR-0015) to spare the encoder.

        Detection modes: the camera in intrinsic capture (``_active_intrinsic``) gets
        board detection + burn-in + telemetry + recording; during a synchronized
        extrinsic sweep (ADR-0007) EVERY camera detects, on a shared wall-clock grid
        so the instants align across cameras, feeds the synchronizer/co-visibility,
        and records its own video + timestamp sidecar. A read failure (USB blip) is
        skipped, not fatal.
        """
        executor = self._capture_executor
        preview_size = _preview_size(target.width, target.height) if target.width else None
        capture_period = 1.0 / target.fps if target.fps else 0.0
        detector: BoardDetector | None = None
        detector_extrinsic = False  # which board the current detector was built for
        last_detection: BoardDetection | None = None
        last_telemetry = 0.0
        last_tick = 0
        last_record = 0.0
        last_push = 0.0
        last_capture = 0.0
        while not publisher.is_disconnected():
            if not await loop.run_in_executor(executor, camera.grab):
                await asyncio.sleep(_EMPTY_READ_BACKOFF_S)
                continue
            now = loop.time()
            # Pace to the capture fps: drop this frame undecoded if we are early.
            if now - last_capture < capture_period:
                await asyncio.sleep(0)
                continue
            last_capture = now
            frame = await loop.run_in_executor(executor, camera.retrieve)
            if frame is None:
                continue
            if preview_size is None:
                preview_size = _preview_size(frame.image.shape[1], frame.image.shape[0])

            sweeping = self._extrinsic is not None  # synchronized recording running
            # Detection also runs on EVERY camera while the operator is on the
            # extrinsic view BEFORE starting the sweep (overlay = "does it detect?"
            # sanity check) — but the synchronizer/co-visibility only feed while
            # actually recording (a preview must not inflate the pair counts).
            extrinsic = sweeping or self._active_view == _EXTRINSIC_VIEW
            active = extrinsic or self._active_intrinsic == target.name
            if not active:
                detector = None
            elif detector is not None and detector_extrinsic != extrinsic:
                detector = None  # board target changed (intrinsic <-> extrinsic sweep)

            # Publish the (downscaled) preview at a capped rate to spare the encoder.
            if now - last_push >= _PUSH_PERIOD_S:
                last_push = now
                if active:
                    if detector is None:
                        detector = self._build_detector(extrinsic=extrinsic)
                        detector_extrinsic = extrinsic
                    # Detection fires on a wall-clock grid: during a sweep all cameras
                    # detect the SAME instant (sync-friendly timestamps, ADR-0007); for
                    # the single intrinsic camera the grid is just its 30 Hz cadence.
                    period = _EXTRINSIC_DETECT_PERIOD_S if extrinsic else _DETECT_PERIOD_S
                    tick = int(now / period)
                    if detector is not None and tick != last_tick:
                        last_tick = tick
                        preview, detection = await loop.run_in_executor(
                            executor, _process_frame, detector, frame.image, preview_size
                        )
                        last_detection = detection
                        if sweeping:
                            covis = self._feed_extrinsic(
                                target.name, frame.timestamp, detection, now
                            )
                            if covis is not None:
                                await publisher.send_data(json.dumps(covis), TELEMETRY_TOPIC)
                        if now - last_telemetry >= _TELEMETRY_PERIOD_S:
                            last_telemetry = now
                            phase = "extrinsic" if extrinsic else "intrinsic"
                            payload = json.dumps(
                                coverage_metrics_payload(target.name, detection, phase)
                            )
                            await publisher.send_data(payload, TELEMETRY_TOPIC)
                    else:
                        # Redraw the last detection (no re-detect) so the overlay is steady.
                        preview = await loop.run_in_executor(
                            executor, _draw_preview, frame.image, last_detection, preview_size
                        )
                    publisher.push(target.name, preview)
                else:
                    small = await loop.run_in_executor(
                        executor, _downscale, frame.image, preview_size
                    )
                    publisher.push(target.name, small)

            # Record the RAW native frame (detection fidelity), throttled, off event loop.
            if now - last_record >= _RECORD_PERIOD_S:
                if sweeping:
                    last_record = now
                    lock = self._extrinsic_locks.get(target.name)
                    recorder = self._extrinsic
                    if lock is not None and recorder is not None:
                        async with lock:
                            if self._extrinsic is recorder:  # not closed meanwhile
                                await loop.run_in_executor(
                                    executor,
                                    recorder.write,
                                    target.name,
                                    frame.image,
                                    frame.timestamp,
                                )
                elif self._active_intrinsic == target.name:
                    last_record = now
                    async with self._recorder_lock:
                        if self._recorder is not None:
                            await loop.run_in_executor(
                                executor, self._recorder.write, frame.image
                            )
            await asyncio.sleep(0)
        logger.info("capture loop %s exiting (disconnected)", target.name)

    @staticmethod
    async def _read_first_frame(
        loop: asyncio.AbstractEventLoop, executor: ThreadPoolExecutor, camera: CameraCapture
    ) -> Frame | None:
        for _ in range(_FIRST_FRAME_ATTEMPTS):
            frame = await loop.run_in_executor(executor, camera.read)
            if frame is not None:
                return frame
            await asyncio.sleep(_FIRST_FRAME_BACKOFF_S)
        return None
