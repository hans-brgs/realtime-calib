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
from calibration_service.recording import VideoRecorder
from calibration_service.session.manager import SessionManager
from calibration_service.telemetry import TELEMETRY_TOPIC, coverage_metrics_payload
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
    preview = draw_overlay(image, detection, resize_factor=1.0)
    return _downscale(preview, preview_size), detection

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
# Board detection + burn-in are expensive at native resolution (~17 ms/frame at
# 1080p); throttle them to ~12 Hz for the live gauges. Raw frames are pushed in
# between so the preview stays smooth.
_DETECT_PERIOD_S = 1 / 12
# Recording rate: ~30 Hz of native frames is ample to select keyframes from, and
# caps the encode load. Writes run off the event loop.
_RECORD_PERIOD_S = 1 / 30
# Preview published to LiveKit is downscaled + rate-capped (ADR-0015): capture stays
# native (for recording/detection), but publishing 4x1080p@60 overloads the CPU VP8
# encoder. Downscaling the preview cuts encode ~4x and the fps cap ~2x.
_PREVIEW_MAX_WIDTH = 960
_PREVIEW_FPS = 30
_PUSH_PERIOD_S = 1 / _PREVIEW_FPS


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

    def __init__(self, config: LiveKitConfig, session_manager: SessionManager) -> None:
        self._config = config
        self._sessions = session_manager
        self._task: asyncio.Task[None] | None = None
        # Track name currently in intrinsic capture: only that camera runs detection +
        # burn-in + telemetry; the others publish raw preview (Phase 3.5).
        self._active_intrinsic: str | None = None
        # Recording the raw sweep of the active camera to disk (ADR-0019), if any.
        # Writes run in the executor; the lock serialises write vs close (stop waits
        # for an in-flight write before finalising the file).
        self._recorder: VideoRecorder | None = None
        self._recorder_lock = asyncio.Lock()

    def set_active_intrinsic(self, name: str | None) -> None:
        """Select the camera whose feed gets board detection/overlay/telemetry."""
        logger.info("active intrinsic camera -> %s", name)
        self._active_intrinsic = name

    async def start_intrinsic_recording(self, camera_name: str) -> None:
        """Make ``camera_name`` active and record its raw sweep to the session folder."""
        await self.stop_intrinsic_recording()
        self.set_active_intrinsic(camera_name)
        camera = next((c for c in self._sessions.current().cameras if c.name == camera_name), None)
        if camera is None:
            raise ValueError(f"unknown camera {camera_name!r}")
        path = self._sessions.intrinsic_video_path(camera_name)
        async with self._recorder_lock:
            self._recorder = VideoRecorder(path, camera.width, camera.height, camera.fps)
        logger.info("recording intrinsic sweep of %s -> %s", camera_name, path)

    async def stop_intrinsic_recording(self) -> int:
        """Finalise the current recording (if any) and return the frame count."""
        async with self._recorder_lock:
            recorder = self._recorder
            self._recorder = None
            if recorder is None:
                return 0
            recorder.close()
            logger.info("stopped recording (%d frames)", recorder.frames)
            return recorder.frames

    def _build_detector(self) -> BoardDetector | None:
        board = self._sessions.current().intrinsic_board
        return BoardDetector(board) if board is not None else None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="camera-publish")

    async def refresh(self) -> None:
        """Stop the current publisher and re-launch from the current session/config."""
        logger.info("refreshing camera publishers")
        await self.stop()
        await self.start()

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        """Reconnect loop: (re)publish all cameras whenever a session ends."""
        while True:
            try:
                await self._publish_session()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("publish session failed; reconnecting")
            await asyncio.sleep(_RECONNECT_BACKOFF_S)

    def _resolve_targets(self) -> list[_PublishTarget]:
        session = self._sessions.current()
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
        """Open cameras, connect once, publish all tracks, run capture loops until disconnect."""
        loop = asyncio.get_running_loop()
        targets = await loop.run_in_executor(None, self._resolve_targets)
        if not targets:
            logger.warning("no camera to publish")
            return

        token = mint_publish_token(
            self._config, identity=_PARTICIPANT_IDENTITY, room=self._config.room_name
        )
        publisher = LiveKitPublisher()
        with contextlib.ExitStack() as cameras:
            opened: list[tuple[_PublishTarget, CameraCapture]] = []
            for i, target in enumerate(targets):
                try:
                    camera = cameras.enter_context(
                        open_camera(
                            target.device_node,
                            target.index,
                            width=target.width or None,
                            height=target.height or None,
                            fps=target.fps or None,
                        )
                    )
                except CameraOpenError:
                    logger.exception("cannot open camera %s; skipping", target.device_node)
                    continue
                opened.append((target, camera))
                if i + 1 < len(targets):
                    await asyncio.sleep(_OPEN_STAGGER_S)  # stagger USB negotiation

            if not opened:
                logger.warning("no camera opened")
                return

            try:
                await publisher.connect(self._config.url, token)
                await publisher.await_connected()  # WebRTC handshake before media (#449)

                capture_tasks: list[asyncio.Task[None]] = []
                for target, camera in opened:
                    first = await self._read_first_frame(loop, camera)
                    if first is None:
                        logger.warning("camera %s produced no frame", target.name)
                        continue
                    height, width = first.image.shape[:2]
                    preview_size = _preview_size(width, height)
                    await publisher.publish_camera_track(
                        target.name, preview_size[0], preview_size[1], _PREVIEW_FPS
                    )
                    publisher.push(target.name, _downscale(first.image, preview_size))
                    publisher.unmute(target.name)
                    capture_tasks.append(
                        asyncio.create_task(
                            self._capture_loop(loop, publisher, target, camera),
                            name=f"capture-{target.name}",
                        )
                    )

                if not capture_tasks:
                    return
                logger.info("publishing %d camera track(s)", len(capture_tasks))
                try:
                    await asyncio.gather(*capture_tasks)
                finally:
                    for task in capture_tasks:
                        task.cancel()
                    for task in capture_tasks:
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
            finally:
                await publisher.aclose()

    async def _capture_loop(
        self,
        loop: asyncio.AbstractEventLoop,
        publisher: LiveKitPublisher,
        target: _PublishTarget,
        camera: CameraCapture,
    ) -> None:
        """Read one camera and push every frame to its track until the room disconnects.

        The preview published to LiveKit is downscaled + rate-capped (ADR-0015) to
        keep the encoder from saturating. The camera in intrinsic capture
        (``_active_intrinsic``) also gets board detection, burn-in overlay, ~10 Hz
        coverage telemetry and disk recording (at native res); the others push raw.
        A read failure (USB blip) is skipped, not fatal.
        """
        preview_size = _preview_size(target.width, target.height) if target.width else None
        detector: BoardDetector | None = None
        last_telemetry = 0.0
        last_detect = 0.0
        last_record = 0.0
        last_push = 0.0
        while not publisher.is_disconnected():
            frame = await loop.run_in_executor(None, camera.read)
            if frame is None:
                await asyncio.sleep(_EMPTY_READ_BACKOFF_S)
                continue
            if preview_size is None:
                preview_size = _preview_size(frame.image.shape[1], frame.image.shape[0])

            now = loop.time()
            active = self._active_intrinsic == target.name
            if not active:
                detector = None

            # Publish the (downscaled) preview at a capped rate to spare the encoder.
            if now - last_push >= _PUSH_PERIOD_S:
                last_push = now
                if active and now - last_detect >= _DETECT_PERIOD_S:
                    if detector is None:
                        detector = self._build_detector()
                    if detector is not None:
                        last_detect = now
                        preview, detection = await loop.run_in_executor(
                            None, _process_frame, detector, frame.image, preview_size
                        )
                        publisher.push(target.name, preview)
                        if now - last_telemetry >= _TELEMETRY_PERIOD_S:
                            last_telemetry = now
                            payload = json.dumps(coverage_metrics_payload(target.name, detection))
                            await publisher.send_data(payload, TELEMETRY_TOPIC)
                    else:
                        publisher.push(target.name, _downscale(frame.image, preview_size))
                else:
                    small = await loop.run_in_executor(None, _downscale, frame.image, preview_size)
                    publisher.push(target.name, small)

            # Record the RAW native frame (detection fidelity), throttled, off event loop.
            if active and now - last_record >= _RECORD_PERIOD_S:
                last_record = now
                async with self._recorder_lock:
                    if self._recorder is not None:
                        await loop.run_in_executor(None, self._recorder.write, frame.image)
            await asyncio.sleep(0)
        logger.info("capture loop %s exiting (disconnected)", target.name)

    @staticmethod
    async def _read_first_frame(
        loop: asyncio.AbstractEventLoop, camera: CameraCapture
    ) -> Frame | None:
        for _ in range(_FIRST_FRAME_ATTEMPTS):
            frame = await loop.run_in_executor(None, camera.read)
            if frame is not None:
                return frame
            await asyncio.sleep(_FIRST_FRAME_BACKOFF_S)
        return None
