"""Publishes every detected camera as a LiveKit video track (Phase 0 loop).

One asyncio task per camera: connect, publish a track, then push frames read in
an executor (capture is blocking). This is the minimal real-time loop; the full
per-camera multiprocessing ``CaptureProcess`` (ADR-0005) comes later.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from calibration_service.capture.camera import CameraCapture, CameraOpenError, open_camera
from calibration_service.capture.enumeration import DetectedCamera, enumerate_cameras
from calibration_service.config import LiveKitConfig
from calibration_service.models.frame import Frame
from calibration_service.transport.livekit_publisher import LiveKitPublisher, mint_publish_token

logger = logging.getLogger(__name__)

# Small sleep when a read returns nothing, to avoid a busy loop on a stalled camera.
_EMPTY_READ_BACKOFF_S = 0.005
# Warm-up budget for the first frame (cameras can take ~1s to start streaming).
_FIRST_FRAME_ATTEMPTS = 60
_FIRST_FRAME_BACKOFF_S = 0.1


class CameraPublishService:
    """Owns one publish task per detected camera for the lifetime of the service."""

    def __init__(self, config: LiveKitConfig) -> None:
        self._config = config
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        cameras = await loop.run_in_executor(None, enumerate_cameras)
        if not cameras:
            logger.warning("no camera detected; nothing to publish")
            return
        for camera in cameras:
            task = asyncio.create_task(
                self._publish_camera(camera), name=f"publish-cam-{camera.index}"
            )
            self._tasks.append(task)
        logger.info("started %d camera publish task(s)", len(self._tasks))

    async def _publish_camera(self, detected: DetectedCamera) -> None:
        track_name = f"cam_{detected.index}"
        identity = f"service-{track_name}"
        token = mint_publish_token(self._config, identity=identity, room=self._config.room_name)

        loop = asyncio.get_running_loop()
        publisher = LiveKitPublisher()
        try:
            with open_camera(
                detected.device_node,
                detected.index,
                width=detected.width,
                height=detected.height,
            ) as camera:
                # Learn the true frame size from the first real frame so the
                # VideoSource matches exactly what we push (no size mismatch).
                first = await self._read_first_frame(loop, camera)
                if first is None:
                    logger.warning("camera %s produced no frame; not publishing", track_name)
                    return
                height, width = first.image.shape[:2]

                await publisher.connect(self._config.url, token)
                await publisher.publish_camera_track(track_name, width, height)
                publisher.push(first.image)

                while True:
                    frame = await loop.run_in_executor(None, camera.read)
                    if frame is None:
                        await asyncio.sleep(_EMPTY_READ_BACKOFF_S)
                        continue
                    publisher.push(frame.image)
                    await asyncio.sleep(0)  # yield to the event loop
        except asyncio.CancelledError:
            raise
        except CameraOpenError:
            logger.exception("cannot open camera %s", detected.device_node)
        except Exception:
            logger.exception("publish task for %s failed", track_name)
        finally:
            await publisher.aclose()

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

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
