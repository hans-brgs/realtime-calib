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

import csv
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


def parse_caliscope_timestamps(path: Path) -> dict[str, list[float]]:
    """Parse a Caliscope ``timestamps.csv`` into per-camera frame times (ADR-0031).

    Format (Caliscope import contract): two named columns ``cam_id,frame_time`` — one
    row per recorded frame, in ANY order; ``cam_id`` is the numeric part of ``cam_<n>``
    and ``frame_time`` a capture instant in seconds. Rows are grouped by ``cam_id`` and
    sorted ascending by ``frame_time`` so each list lines up with the video's frame
    index — ready to write as a ``<cam>.timestamps`` sidecar. Read by column NAME
    (``DictReader``) so extra columns / column order don't matter. Raises
    ``ValueError`` on a missing header or a non-numeric ``frame_time``.
    """
    by_camera: dict[str, list[float]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if "cam_id" not in fields or "frame_time" not in fields:
            raise ValueError("timestamps.csv must have 'cam_id' and 'frame_time' columns")
        for line_no, row in enumerate(reader, start=2):  # line 1 is the header
            cam_id = (row.get("cam_id") or "").strip()
            raw = (row.get("frame_time") or "").strip()
            if not cam_id and not raw:
                continue  # tolerate blank trailing lines
            try:
                frame_time = float(raw)
            except ValueError as exc:
                raise ValueError(
                    f"timestamps.csv line {line_no}: invalid frame_time {raw!r}"
                ) from exc
            by_camera.setdefault(cam_id, []).append(frame_time)
    for times in by_camera.values():
        times.sort()
    return by_camera
