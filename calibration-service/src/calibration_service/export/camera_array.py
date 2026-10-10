"""Calibration export documents: Caliscope + aniposelib TOMLs, per-platform JSON variants.

Spec [[calibration-export]] / ADR-0002, ADR-0047. The canonical ``camera_array.toml``
is Caliscope's native layout with its field semantics untouched (native fields
as-is, extensions additive); ``camera_array_aniposelib.toml`` is the layout
downstream tools read, written by Caliscope next to its own;
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

from calibration_service.resolution import output_size
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


@dataclass(frozen=True)
class ExportTarget:
    """A selectable export artifact for the export screen (ADR-0026)."""

    id: str  # "caliscope" | "aniposelib" | platform format id
    filename: str
    kind: str  # "toml" | "json" — drives the code-highlight language
    label: str  # sub-label: destination + axes/handedness
    up: str  # "y" | "z" | "" (the TOMLs keep OpenCV axes)
    handedness: str  # "right" | "left" | ""


def export_targets() -> list[ExportTarget]:
    """Catalog of selectable export targets — the backend is the single source
    (ADR-0026): the webapp fetches this instead of a parallel client-side copy."""
    targets = [
        ExportTarget(
            id="caliscope",
            filename="camera_array.toml",
            kind="toml",
            label="Caliscope · OpenCV axes",
            up="",
            handedness="",
        ),
        ExportTarget(
            id="aniposelib",
            # Caliscope v0.11.5's own name for this file, next to camera_array.toml.
            filename="camera_array_aniposelib.toml",
            kind="toml",
            # Its consumers, as Caliscope v0.11.5's README names them.
            label="aniposelib (Pose2Sim, anipose) · OpenCV axes",
            up="",
            handedness="",
        ),
    ]
    for format_id, convention in CONVENTIONS.items():
        up_label = "Y-up" if convention.up == "y" else "Z-up"
        targets.append(
            ExportTarget(
                id=format_id,
                filename=f"camera_array_{format_id}.json",
                kind="json",
                label=f"{convention.platforms} · {up_label} · {convention.handedness}-handed",
                up=convention.up,
                handedness=convention.handedness,
            )
        )
    return targets


# Export unit -> factor applied to translations held in millimetres (ADR-0026).
_UNIT_SCALE = {"mm": 1.0, "m": 0.001}


def _unit_scale(units: str) -> float:
    """Millimetre-to-``units`` factor; an unknown unit is refused, not defaulted.

    It used to fall through to millimetres: a session carrying ``"km"`` wrote a mm
    TOML while the JSON announced ``world_units: "km"``.
    """
    try:
        return _UNIT_SCALE[units]
    except KeyError:
        expected = ", ".join(sorted(_UNIT_SCALE))
        raise ValueError(f"unknown export units {units!r} (expected: {expected})") from None


def _output_size(camera: CameraConfig) -> list[int]:
    """Calibration (output) resolution the stored K corresponds to (ADR-0015)."""
    return list(output_size((camera.width, camera.height), camera.resize_factor or 1.0))


def _translation_mm(camera: CameraConfig, square_size_mm: float) -> list[float]:
    """Extrinsic translation scaled from board squares to millimetres.

    Fail loud on a missing translation (ADR-0036): exporting it as the world
    ORIGIN produced a plausible-looking file with a teleported camera. The API
    guards normally prevent this, but a silently wrong export is worse than a
    refused one.
    """
    if camera.translation is None:
        raise ValueError(f"camera {camera.name} has no extrinsic translation — recompute first")
    return [float(v) * square_size_mm for v in camera.translation]


def caliscope_document(session: CalibrationSession, square_size_mm: float) -> dict[str, Any]:
    """``camera_array.toml`` in Caliscope's NATIVE layout (ADR-0047).

    ``[cameras.<id>]`` tables, as Caliscope v0.11.5's ``CameraArray.to_toml``
    writes them and ``from_toml`` reads them. The former top-level ``[cam_N]``
    layout loaded as an EMPTY array, silently: ``from_toml`` returns
    ``CameraArray({})`` for any file without a ``cameras`` table (so since 0.7).

    Native fields keep Caliscope's semantics (ADR-0002): translations in METRES
    whatever the export units — Caliscope's world unit, a mm value would read
    1000x too large; rotation as a Rodrigues 3-vector; ``rotation_count = 0``
    (no rotated sensor); ``fisheye = false``; ``distortions`` exactly as
    calibrated, the classic 5 coefficients since ADR-0032. ``name`` and
    ``device_path`` are additive extensions (``from_toml`` reads keys by name);
    ``device_path`` reconciles camera id -> physical device without our session.
    """
    cameras: dict[str, Any] = {}
    for camera in session.cameras:
        entry: dict[str, Any] = {
            "cam_id": camera.index,
            "size": _output_size(camera),
            "rotation_count": 0,
            "error": camera.calibration_error,
            "matrix": camera.matrix,
            "distortions": camera.distortions,
            "translation": _translation(camera, square_size_mm, "m"),
            "rotation": _rotation(camera),
            "grid_count": camera.grid_count,
            "fisheye": False,
            "name": camera.name,
            "device_path": camera.device_path,
        }
        cameras[str(camera.index)] = {k: v for k, v in entry.items() if v is not None}
    return {"cameras": cameras}


def aniposelib_document(
    session: CalibrationSession, square_size_mm: float, units: str = "m"
) -> dict[str, Any]:
    """``camera_array_aniposelib.toml``: top-level ``[cam_N]`` tables (ADR-0047).

    The layout Caliscope v0.11.5 writes next to its native file for downstream
    tools (``to_aniposelib_toml``): ``aniposelib.CameraGroup.load`` reads
    ``name``, ``size``, ``matrix``, ``distortions``, ``rotation``,
    ``translation`` and ``fisheye`` per table and skips ``metadata``. The tables
    also carry what Caliscope <= 0.5.4 read from its ``config.toml`` (``port``,
    ``rotation_count``, ``error``, ``grid_count``), so a METRE export pastes into
    a legacy project unchanged — in mm its world would read 1000x too large.
    aniposelib has no unit of its own: translations follow the export ``units``
    (metres by default — what Caliscope writes).
    """
    document: dict[str, Any] = {}
    for camera in session.cameras:
        entry: dict[str, Any] = {
            "name": camera.name,
            "size": _output_size(camera),
            "matrix": camera.matrix,
            "distortions": camera.distortions,
            "rotation": _rotation(camera),
            "translation": _translation(camera, square_size_mm, units),
            "fisheye": False,
            "port": camera.index,
            "rotation_count": 0,
            "error": camera.calibration_error,
            "grid_count": camera.grid_count,
            "device_path": camera.device_path,
        }
        document[f"cam_{camera.index}"] = {k: v for k, v in entry.items() if v is not None}
    # Not adjusted by anipose's own bundle adjustment (what Caliscope declares too).
    document["metadata"] = {"adjusted": False}
    return document


def _rotation(camera: CameraConfig) -> list[float]:
    """The world->camera Rodrigues vector; fail loud when it is missing (ADR-0036)."""
    if camera.rotation is None:
        raise ValueError(f"camera {camera.name} has no extrinsic rotation — recompute first")
    return list(camera.rotation)


def _translation(camera: CameraConfig, square_size_mm: float, units: str) -> list[float]:
    """The world->camera translation in ``units`` (from board squares)."""
    scale = _unit_scale(units)
    return [scale * v for v in _translation_mm(camera, square_size_mm)]


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
    session: CalibrationSession, format_id: str, square_size_mm: float, units: str = "mm"
) -> dict[str, Any]:
    """A self-describing per-platform JSON document (spec calibration-export).

    Each camera carries the SCENE form (position/quaternion/matrix, camera->world
    — what a scene graph applies to place the object) and, for right-handed
    conventions only, the VIEW form (R|t, world->camera — what an OpenCV-style
    consumer feeds to projection). Left-handed conventions get no view block:
    the raw view rotation R @ M^T has det=-1 there (a mirror, not a rotation) —
    project via the platform's own camera API instead. ``units`` scales world
    lengths ("mm" or "m"); intrinsics stay in pixels.
    """
    convention = CONVENTIONS[format_id]
    basis = np.asarray(convention.basis, np.float64)
    forward = basis @ np.array([0.0, 0.0, 1.0])  # OpenCV optical axis, remapped
    local_up = basis @ np.array([0.0, -1.0, 0.0])  # OpenCV "up" (-y), remapped

    cameras: list[dict[str, Any]] = []
    for camera in session.cameras:
        rotation_w2c = np.asarray(cv2.Rodrigues(np.asarray(_rotation(camera)))[0], np.float64)
        translation = np.asarray(_translation(camera, square_size_mm, units), np.float64)
        position = basis @ (-rotation_w2c.T @ translation)
        # Similarity keeps det=+1 under a mirror: the ONE place the LH flip happens.
        rotation_c2w = basis @ rotation_w2c.T @ basis.T
        matrix = np.eye(4)
        matrix[:3, :3] = rotation_c2w
        matrix[:3, 3] = position

        size = _output_size(camera)
        fy = float(camera.matrix[1][1]) if camera.matrix else 0.0
        fov_deg = 2.0 * math.degrees(math.atan(size[1] / (2.0 * fy))) if fy else 0.0
        entry: dict[str, Any] = {
            "name": camera.name,
            # Stable v4l identifier: reconcile camera id -> physical device.
            "device_path": camera.device_path,
            "position": [float(v) for v in position],
            "quaternion": _quaternion_xyzw(rotation_c2w),
            "matrix": [[float(v) for v in row] for row in matrix],
            "intrinsics": {
                "resolution": size,
                "matrix": camera.matrix,
                "distortions": camera.distortions,
                "fov_deg": round(fov_deg, 3),
            },
            "error": (
                camera.extrinsic_error
                if camera.extrinsic_error is not None
                else camera.calibration_error
            ),
        }
        if convention.handedness == "right":
            entry["view"] = {
                "R": [[float(v) for v in row] for row in rotation_w2c @ basis.T],
                "t": [float(v) for v in translation],
            }
        cameras.append(entry)

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
        "world_units": units,
        # The gauge camera the solver holds fixed: the LOWEST index (ADR-0012) —
        # an imported cam_1..N rig has no cam_0, and the field used to read null.
        "anchor": min(session.cameras, key=lambda c: c.index).name if session.cameras else None,
        "cameras": cameras,
    }
