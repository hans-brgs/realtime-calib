"""Calibration export documents: Caliscope TOML + per-platform JSON variants.

Spec [[calibration-export]] / ADR-0002. The canonical ``camera_array.toml`` keeps
Caliscope's field semantics untouched (native fields as-is, extensions additive);
the platform variants are **integration files, not engine assets** — every target
still needs a ~10-line loader, but the dangerous 3D math (axis remap, left-handed
mirror, quaternion convention) is done here and each file is self-describing.

Pose math. Canonical data (ADR-0023): ``x_cam = R x_world + t`` (world = anchor
frame, OpenCV axes), translations in board squares. A platform basis ``M``
(canonical -> platform, det = -1 for left-handed targets) converts a camera's
cam->world pose as ``position' = M (-R^T t)`` and ``R'_c2w = M R^T M^T`` — the
similarity keeps det(R') = +1 even under a mirror, so the quaternion is always
well-defined; the mirror is applied exactly once, through ``M``. The camera's
LOCAL frame is remapped by the same basis, so the convention block publishes
``camera_forward``/``camera_up`` (= M.z_cv / M.(-y_cv)): for Unity, Unreal and
three.js these land exactly on the engine's native camera axes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.models.session import CalibrationSession, CameraConfig


@dataclass(frozen=True)
class Convention:
    """A target platform's world basis, relative to the canonical OpenCV frame."""

    name: str
    label: str
    up: str  # "y" | "z"
    handedness: str  # "right" | "left"
    platforms: str
    basis: tuple[tuple[float, float, float], ...]  # M rows: canonical -> platform
    mapping: str  # human-readable axis mapping (documents the single mirror)


CONVENTIONS: dict[str, Convention] = {
    "threejs": Convention(
        name="yup-rh",
        label="Y-up · right-handed",
        up="y",
        handedness="right",
        platforms="three.js / OpenGL",
        basis=((1, 0, 0), (0, -1, 0), (0, 0, -1)),
        mapping="x -> x, y -> -y, z -> -z",
    ),
    "blender": Convention(
        name="zup-rh",
        label="Z-up · right-handed",
        up="z",
        handedness="right",
        platforms="Blender / ROS",
        basis=((1, 0, 0), (0, 0, 1), (0, -1, 0)),
        mapping="x -> x, z -> y, y -> -z",
    ),
    "unity": Convention(
        name="yup-lh",
        label="Y-up · left-handed",
        up="y",
        handedness="left",
        platforms="Unity",
        basis=((1, 0, 0), (0, -1, 0), (0, 0, 1)),
        mapping="x -> x, y -> -y, z -> z (mirror)",
    ),
    "unreal": Convention(
        name="zup-lh",
        label="Z-up · left-handed",
        up="z",
        handedness="left",
        platforms="Unreal",
        basis=((0, 0, 1), (1, 0, 0), (0, -1, 0)),
        mapping="z -> x (forward), x -> y (right), y -> -z (up) (mirror)",
    ),
}

# formats accepted by POST /export; the canonical Caliscope TOML is always written.
PLATFORM_FORMATS = tuple(CONVENTIONS)


def _output_size(camera: CameraConfig) -> list[int]:
    """Calibration (output) resolution the stored K corresponds to (ADR-0015)."""
    factor = camera.resize_factor or 1.0
    return [round(camera.width * factor), round(camera.height * factor)]


def _translation_mm(camera: CameraConfig, square_size_mm: float) -> list[float]:
    """Extrinsic translation scaled from board squares to millimetres."""
    return [float(v) * square_size_mm for v in camera.translation or [0.0, 0.0, 0.0]]


def caliscope_document(session: CalibrationSession, square_size_mm: float) -> dict[str, Any]:
    """The canonical ``camera_array.toml`` content (Caliscope semantics, ADR-0002).

    ``distortions`` is written exactly as calibrated — 8 rational-model
    coefficients under ``CALIB_RATIONAL_MODEL`` (Caliscope's own flags), which
    OpenCV consumers accept like the classic 5.
    """
    document: dict[str, Any] = {}
    for camera in session.cameras:
        entry: dict[str, Any] = {
            "port": camera.index,
            "name": camera.name,  # additive extension
            "size": _output_size(camera),
            "matrix": camera.matrix,
            "distortions": camera.distortions,
            "error": camera.calibration_error,
            "grid_count": camera.grid_count,
        }
        if camera.rotation is not None:
            entry["rotation"] = camera.rotation
            entry["translation"] = _translation_mm(camera, square_size_mm)
        document[f"cam_{camera.index}"] = {k: v for k, v in entry.items() if v is not None}
    return document


def aniposelib_document(
    session: CalibrationSession, square_size_mm: float, overall_error: float | None
) -> dict[str, Any]:
    """``camera_array_aniposelib.toml``: the aniposelib CameraGroup layout."""
    document: dict[str, Any] = {}
    for camera in session.cameras:
        document[f"cam_{camera.index}"] = {
            "name": camera.name,
            "size": _output_size(camera),
            "matrix": camera.matrix,
            "distortions": camera.distortions,
            "rotation": camera.rotation or [0.0, 0.0, 0.0],
            "translation": _translation_mm(camera, square_size_mm),
            "fisheye": False,
        }
    document["metadata"] = {"adjusted": True, "error": overall_error or 0.0}
    return document


def _quaternion_xyzw(rotation: NDArray[np.float64]) -> list[float]:
    """Quaternion (x, y, z, w) from a proper rotation matrix (Shepperd's method)."""
    trace = float(np.trace(rotation))
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w = 0.25 * s
        x = (rotation[2, 1] - rotation[1, 2]) / s
        y = (rotation[0, 2] - rotation[2, 0]) / s
        z = (rotation[1, 0] - rotation[0, 1]) / s
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        s = math.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2
        w = (rotation[2, 1] - rotation[1, 2]) / s
        x = 0.25 * s
        y = (rotation[0, 1] + rotation[1, 0]) / s
        z = (rotation[0, 2] + rotation[2, 0]) / s
    elif rotation[1, 1] > rotation[2, 2]:
        s = math.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2
        w = (rotation[0, 2] - rotation[2, 0]) / s
        x = (rotation[0, 1] + rotation[1, 0]) / s
        y = 0.25 * s
        z = (rotation[1, 2] + rotation[2, 1]) / s
    else:
        s = math.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2
        w = (rotation[1, 0] - rotation[0, 1]) / s
        x = (rotation[0, 2] + rotation[2, 0]) / s
        y = (rotation[1, 2] + rotation[2, 1]) / s
        z = 0.25 * s
    return [float(x), float(y), float(z), float(w)]


def platform_variant(
    session: CalibrationSession, format_id: str, square_size_mm: float
) -> dict[str, Any]:
    """A self-describing per-platform JSON document (spec calibration-export)."""
    convention = CONVENTIONS[format_id]
    basis = np.asarray(convention.basis, np.float64)
    forward = basis @ np.array([0.0, 0.0, 1.0])  # OpenCV optical axis, remapped
    local_up = basis @ np.array([0.0, -1.0, 0.0])  # OpenCV "up" (-y), remapped

    cameras: list[dict[str, Any]] = []
    for camera in session.cameras:
        rotation_w2c = np.asarray(
            cv2.Rodrigues(np.asarray(camera.rotation or [0.0, 0.0, 0.0]))[0], np.float64
        )
        translation = np.asarray(_translation_mm(camera, square_size_mm), np.float64)
        position = basis @ (-rotation_w2c.T @ translation)
        # Similarity keeps det=+1 under a mirror: the ONE place the LH flip happens.
        rotation_c2w = basis @ rotation_w2c.T @ basis.T
        matrix = np.eye(4)
        matrix[:3, :3] = rotation_c2w
        matrix[:3, 3] = position

        size = _output_size(camera)
        fy = float(camera.matrix[1][1]) if camera.matrix else 0.0
        fov_deg = 2.0 * math.degrees(math.atan(size[1] / (2.0 * fy))) if fy else 0.0
        cameras.append(
            {
                "name": camera.name,
                "position": [float(v) for v in position],
                "quaternion": _quaternion_xyzw(rotation_c2w),
                "matrix": [[float(v) for v in row] for row in matrix],
                "intrinsics": {
                    "resolution": size,
                    "matrix": camera.matrix,
                    "distortions": camera.distortions,
                    "fov_deg": round(fov_deg, 3),
                },
                "error": camera.extrinsic_error or camera.calibration_error,
            }
        )

    return {
        "convention": {
            "name": convention.name,
            "label": convention.label,
            "up": convention.up,
            "handedness": convention.handedness,
            "platforms": convention.platforms,
            "mapping": convention.mapping,
            "camera_forward": [float(v) for v in forward],
            "camera_up": [float(v) for v in local_up],
        },
        "world_units": "mm",
        "anchor": next((c.name for c in session.cameras if c.index == 0), None),
        "cameras": cameras,
    }
