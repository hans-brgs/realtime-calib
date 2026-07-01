"""Manual end-to-end check: publish one camera to LiveKit for a few seconds.

    uv run python -m calibration_service.transport.publish_camera

Requires a reachable LiveKit server (``LIVEKIT_URL``) and a camera. Used to
verify the publish path against a live SFU.
"""

from __future__ import annotations

import asyncio
import logging

from calibration_service.capture.camera import open_camera
from calibration_service.capture.enumeration import enumerate_cameras
from calibration_service.config import LiveKitConfig
from calibration_service.logging_setup import setup_logging
from calibration_service.transport.livekit_publisher import LiveKitPublisher, mint_publish_token

logger = logging.getLogger(__name__)

PUBLISH_SECONDS = 10.0


async def _run() -> None:
    config = LiveKitConfig()

    cameras = enumerate_cameras()
    if not cameras:
        logger.warning("no camera detected")
        return
    target = cameras[0]
    track_name = f"cam_{target.index}"

    token = mint_publish_token(config, identity=f"service-{track_name}", room=config.room_name)
    publisher = LiveKitPublisher()
    await publisher.connect(config.url, token)
    await publisher.publish_camera_track(track_name, target.width, target.height, fps=30)
    publisher.unmute(track_name)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + PUBLISH_SECONDS
    pushed = 0
    with open_camera(target.device_node, target.index) as camera:
        while loop.time() < deadline:
            frame = await loop.run_in_executor(None, camera.read)
            if frame is None:
                continue
            publisher.push(track_name, frame.image)
            pushed += 1
            await asyncio.sleep(0)  # yield to the event loop
    logger.info("pushed %d frames; closing", pushed)
    await publisher.aclose()


def main() -> None:
    setup_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
