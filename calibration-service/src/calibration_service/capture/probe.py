"""Manual capture smoke test: ``uv run python -m calibration_service.capture.probe``.

Enumerates cameras, then captures a burst from the first one and logs the
measured fps and frame size. Used to verify real USB capture on the host.
"""

from __future__ import annotations

import logging
import time

from calibration_service.capture.camera import CameraOpenError, open_camera
from calibration_service.capture.enumeration import enumerate_cameras
from calibration_service.logging_setup import setup_logging

logger = logging.getLogger(__name__)

FRAMES_TO_MEASURE = 120


def main() -> None:
    setup_logging()

    cameras = enumerate_cameras()
    if not cameras:
        logger.warning("no camera detected")
        return

    for cam in cameras:
        logger.info(
            "detected camera index=%d path=%s node=%s %dx%d @ %.1f fps",
            cam.index,
            cam.device_path,
            cam.device_node,
            cam.width,
            cam.height,
            cam.fps,
        )

    target = cameras[0]
    logger.info(
        "capturing %d frames from camera index=%d (%s)",
        FRAMES_TO_MEASURE,
        target.index,
        target.device_path,
    )

    try:
        camera = open_camera(target.device_node, target.index)
    except CameraOpenError:
        logger.exception("failed to open camera %s", target.device_node)
        return

    captured = 0
    dropped = 0
    last_width = 0
    last_height = 0
    started = time.monotonic()
    with camera:
        for _ in range(FRAMES_TO_MEASURE):
            frame = camera.read()
            if frame is None:
                dropped += 1
                continue
            captured += 1
            last_height, last_width = frame.image.shape[:2]
    elapsed = time.monotonic() - started

    if captured == 0:
        logger.warning("no frames captured (%d dropped)", dropped)
        return

    measured_fps = captured / elapsed if elapsed > 0 else 0.0
    logger.info(
        "captured %d frames (%d dropped) in %.2fs -> %.1f fps measured, frame %dx%d",
        captured,
        dropped,
        elapsed,
        measured_fps,
        last_width,
        last_height,
    )


if __name__ == "__main__":
    main()
