"""Record the synchronized extrinsic sweep: one video + timestamp sidecar per camera.

Spec [[calibration-recording]] / ADR-0007: ``extrinsic/<cam>.mkv`` (MJPG, same
rationale as the intrinsic capture) plus ``extrinsic/<cam>.timestamps`` — one
host-monotonic timestamp per written frame, line-aligned with the video frame
index. The sidecars are the ONLY way to re-synchronize the recordings at compute
or replay time (frame numbers are not comparable across free-running cameras).
A ``manifest.json`` lists the per-camera artifacts so the compute can open the
set without the session object.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from calibration_service.recording.video_writer import VideoRecorder
from calibration_service.session.store import session_dir

logger = logging.getLogger(__name__)

_EXTRINSIC_DIR = "extrinsic"
_MANIFEST_FILE = "manifest.json"


def extrinsic_dir(sessions_dir: Path, session_id: str) -> Path:
    """``<sessions_dir>/<session_id>/extrinsic``."""
    return session_dir(sessions_dir, session_id) / _EXTRINSIC_DIR


@dataclass(frozen=True)
class CameraSpec:
    """Recording parameters of one camera in the synchronized sweep."""

    name: str
    width: int
    height: int
    fps: int


class ExtrinsicRecorder:
    """N per-camera recorders + timestamp sidecars for one synchronized sweep."""

    def __init__(self, directory: Path, cameras: list[CameraSpec]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory
        self._specs = list(cameras)
        self._recorders = {
            spec.name: VideoRecorder(
                directory / f"{spec.name}.mkv", spec.width, spec.height, spec.fps
            )
            for spec in cameras
        }
        # Sidecars stay open for appending; text mode, one "%.6f" per line.
        self._sidecars = {
            spec.name: (directory / f"{spec.name}.timestamps").open("w", encoding="ascii")
            for spec in cameras
        }

    def write(self, camera: str, image: NDArray[np.uint8], timestamp: float) -> None:
        """Append one frame + its capture timestamp for ``camera``.

        Called concurrently from different capture loops — safe because each
        camera touches only its own writer + sidecar (no shared state).
        """
        recorder = self._recorders.get(camera)
        sidecar = self._sidecars.get(camera)
        if recorder is None or sidecar is None:
            return
        recorder.write(image)
        sidecar.write(f"{timestamp:.6f}\n")

    def frames(self) -> dict[str, int]:
        return {name: recorder.frames for name, recorder in self._recorders.items()}

    def close(self) -> dict[str, int]:
        """Finalise every video + sidecar, write the manifest, return frame counts."""
        counts = self.frames()
        for recorder in self._recorders.values():
            recorder.close()
        for sidecar in self._sidecars.values():
            sidecar.close()
        manifest = {
            "cameras": [
                {
                    "name": spec.name,
                    "video": f"{spec.name}.mkv",
                    "timestamps": f"{spec.name}.timestamps",
                    "width": spec.width,
                    "height": spec.height,
                    "fps": spec.fps,
                    "frames": counts[spec.name],
                }
                for spec in self._specs
            ]
        }
        (self._directory / _MANIFEST_FILE).write_text(json.dumps(manifest, indent=2))
        logger.info("extrinsic sweep closed: %s", counts)
        return counts


def read_timestamps(path: Path) -> list[float]:
    """Read a sidecar back into per-frame timestamps (compute/replay side)."""
    return [float(line) for line in path.read_text(encoding="ascii").split() if line]
