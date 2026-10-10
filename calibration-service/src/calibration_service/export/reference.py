"""Re-alignment of the world on a reference calibration (ADR-0061).

A reference is a previous calibration of the same room, world -> camera in the export
world (Y up, right-handed, metres): ``camera_array_opencv.json`` version 1, or the bare
``{"cameras": [{"port", "R", "t"}]}`` it grew from. Only the optical centres serve.
The fit maps this solve's centres, expressed in the export world, onto the reference's:
``floor`` keeps the framed floor (a rotation about the vertical and a horizontal
translation), ``rigid`` is a 6-DoF Kabsch without scale. Neither applies a scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic import (
    ExtrinsicResult,
    board_unit_mm,
    camera_centres,
)
from calibration_service.export.opencv import BASIS, FORMAT, VERSION, WorldFrame
from calibration_service.models.board import CalibrationBoard
from calibration_service.models.session import CalibrationSession

# A recalibration of the same room moves by a few degrees; a port permutation by 180.
MAX_ROTATION_DEG = 20.0
# Residual RMS of the fit: the same room re-calibrated leaves ~5 cm (Test on the dev
# room's previous calibration), the 6 distinct calibrations of other sites with the
# same four-camera layout 13 to 39 cm. It does not reliably see one moved camera.
MAX_RESIDUAL_M = 0.10
# Reference spread over ours (Umeyama): outside, units or cameras disagree.
SCALE_BOUNDS = (0.8, 1.25)
# The rigid fit needs centres spread in two directions: s2 / s1 of the centred set.
MIN_SPREAD_RATIO = 0.1
_IMPORTED_PREFIX = "import:"
_UP = np.array([0.0, 1.0, 0.0])


@dataclass(frozen=True)
class ReferenceCamera:
    port: int
    device_path: str | None
    centre_m: list[float]  # optical centre, export world


@dataclass(frozen=True)
class Reference:
    name: str
    format: str  # "opencv-v1" | "bare"
    cameras: list[ReferenceCamera]


@dataclass(frozen=True)
class AlignmentReport:
    """One mode's fit of this solve onto the reference, or why it is refused."""

    mode: str  # "floor" | "rigid"
    refused: str | None  # the reason, None when the fit applies
    matched_by: str | None = None  # "device_path" | "port"
    cameras: list[str] = field(default_factory=list)  # matched, this session's names
    rotation_deg: float | None = None
    translation_m: list[float] | None = None
    residual_rms_m: float | None = None
    residuals_m: dict[str, float] = field(default_factory=dict)
    vertical_offset_m: float | None = None  # floor: measured, not applied
    tilt_deg: float | None = None  # floor: what the 6-DoF fit would tilt; rigid: its tilt
    angle_sigma_deg: float | None = None  # rigid: residual RMS over the centres' spread
    scale_ratio: float | None = None  # reported, never applied


def _numbers(value: Any, what: str) -> NDArray[np.float64]:
    if value is None:
        raise ValueError(f"{what} is missing")
    try:
        array = np.asarray(value, np.float64)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must hold numbers") from None
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{what} must hold finite numbers")
    return array


def _rotation_matrix(value: Any, port: int) -> NDArray[np.float64]:
    matrix = _numbers(value, f"camera port {port}: R")
    if matrix.shape != (3, 3):
        raise ValueError(f"camera port {port}: R must be a 3x3 matrix")
    if not np.allclose(matrix @ matrix.T, np.eye(3), atol=1e-4) or np.linalg.det(matrix) < 0:
        raise ValueError(f"camera port {port}: R must be a proper rotation")
    return matrix


def parse_reference(document: Any, name: str) -> Reference:
    """Normalise a reference document, or raise ValueError saying why it is refused."""
    if not isinstance(document, dict) or not isinstance(document.get("cameras"), list):
        raise ValueError("a reference is a JSON object with a 'cameras' list")
    declared = document.get("format")
    if declared is None:
        kind = "bare"
    elif declared == FORMAT and document.get("version") == VERSION:
        kind = "opencv-v1"
    else:
        raise ValueError(
            f"unsupported reference format {declared!r} version {document.get('version')!r}"
        )
    cameras: list[ReferenceCamera] = []
    for entry in document["cameras"]:
        port = entry.get("port") if isinstance(entry, dict) else None
        if not isinstance(port, int) or isinstance(port, bool):
            raise ValueError("every reference camera needs an integer 'port'")
        rotation = _rotation_matrix(entry.get("R"), port)
        translation = _numbers(entry.get("t"), f"camera port {port}: t")
        if translation.shape not in ((3,), (3, 1)):
            raise ValueError(f"camera port {port}: t must hold 3 values")
        translation = translation.reshape(3)
        device = entry.get("device_path")
        cameras.append(
            ReferenceCamera(
                entry["port"],
                device if isinstance(device, str) and device else None,
                [float(v) for v in -rotation.T @ translation],
            )
        )
    ports = [c.port for c in cameras]
    if len(set(ports)) != len(ports):
        raise ValueError("duplicate ports in the reference")
    devices = [c.device_path for c in cameras if c.device_path]
    if len(set(devices)) != len(devices):
        raise ValueError("duplicate device paths in the reference")
    if len(cameras) < 2:
        raise ValueError("a reference needs at least 2 cameras")
    return Reference(name, kind, cameras)


def _export_centres(
    result: ExtrinsicResult, board: CalibrationBoard
) -> dict[str, NDArray[np.float64]]:
    """This solve's optical centres in the export world, metres."""
    scale = board_unit_mm(board) / 1000.0
    return {n: BASIS @ c * scale for n, c in camera_centres(result).items()}


def _match(
    reference: Reference, session: CalibrationSession, names: set[str]
) -> tuple[str, list[tuple[str, NDArray[np.float64]]]]:
    """(key, [(this session's camera name, reference centre)]); ValueError when none."""
    cameras = [c for c in session.cameras if c.name in names]
    by_device = {c.device_path: c for c in reference.cameras if c.device_path}
    devices = all(not c.device_path.startswith(_IMPORTED_PREFIX) for c in cameras)
    if by_device and devices:
        pairs = [
            (c.name, np.asarray(by_device[c.device_path].centre_m))
            for c in cameras
            if c.device_path in by_device
        ]
        if not pairs:
            raise ValueError("no camera of the reference matches this rig's device paths")
        return "device_path", pairs
    by_port = {c.port: c for c in reference.cameras}
    pairs = [(c.name, np.asarray(by_port[c.index].centre_m)) for c in cameras if c.index in by_port]
    return "port", pairs


def _kabsch(
    source: NDArray[np.float64], target: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
    """Rotation, translation (target ~ R source + t) and the Umeyama scale."""
    mean_s, mean_t = source.mean(axis=0), target.mean(axis=0)
    u, s, vt = np.linalg.svd((source - mean_s).T @ (target - mean_t))
    sign = float(np.sign(np.linalg.det(vt.T @ u.T))) or 1.0
    correction = np.ones(len(s))
    correction[-1] = sign
    rotation = vt.T @ np.diag(correction) @ u.T
    spread = float(np.sum((source - mean_s) ** 2))
    scale = float(np.sum(s * correction) / spread) if spread > 0 else math.nan
    return rotation, mean_t - rotation @ mean_s, scale


def _angle_deg(rotation: NDArray[np.float64]) -> float:
    return math.degrees(math.acos(float(np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0))))


def _tilt_deg(rotation: NDArray[np.float64]) -> float:
    return math.degrees(math.acos(float(np.clip((rotation @ _UP) @ _UP, -1.0, 1.0))))


@dataclass(frozen=True)
class Alignment:
    report: AlignmentReport
    transform: NDArray[np.float64] | None  # 4x4 world change in the solver's frame


def align(
    result: ExtrinsicResult,
    session: CalibrationSession,
    board: CalibrationBoard,
    reference: Reference,
    world: WorldFrame,
    mode: str,
) -> Alignment:
    """Fit this solve onto ``reference`` in ``mode`` ("floor" or "rigid")."""

    def refused(reason: str, **known: Any) -> Alignment:
        return Alignment(AlignmentReport(mode, reason, **known), None)

    ours = _export_centres(result, board)
    try:
        matched_by, pairs = _match(reference, session, set(ours))
    except ValueError as exc:
        return refused(str(exc))
    names = [name for name, _ in pairs]
    needed = 2 if mode == "floor" else 3
    if len(pairs) < needed:
        return refused(
            f"{mode} needs {needed} matched cameras, {len(pairs)} matched",
            matched_by=matched_by,
            cameras=names,
        )
    source = np.stack([ours[n] for n in names])
    target = np.stack([centre for _, centre in pairs])
    rotation6, translation6, scale = _kabsch(source, target)
    singular = np.linalg.svd(source - source.mean(axis=0), compute_uv=False)
    coplanar_ok = len(pairs) >= 3 and singular[1] >= MIN_SPREAD_RATIO * singular[0]
    known: dict[str, Any] = {"matched_by": matched_by, "cameras": names, "scale_ratio": scale}
    if mode == "floor":
        if world.up != "y":
            return refused(
                "floor mode needs a world framed on a level floor target: frame it, or use rigid",
                **known,
            )
        # A rotation about the vertical: a 2D Procrustes on the horizontal (x, z) plane.
        flat_s, flat_t = source[:, [0, 2]], target[:, [0, 2]]
        planar, _, _ = _kabsch(flat_s, flat_t)
        rotation = np.array(
            [[planar[0, 0], 0.0, planar[0, 1]], [0.0, 1.0, 0.0], [planar[1, 0], 0.0, planar[1, 1]]]
        )
        shift = target.mean(axis=0) - rotation @ source.mean(axis=0)
        known["vertical_offset_m"] = float(shift[1])
        known["tilt_deg"] = _tilt_deg(rotation6) if coplanar_ok else None
        shift[1] = 0.0
    else:
        if not coplanar_ok:
            return refused("rigid mode needs 3 cameras not on one line", **known)
        rotation, shift = rotation6, translation6
        known["tilt_deg"] = _tilt_deg(rotation6)
    moved = source @ rotation.T + shift
    residuals = np.linalg.norm(moved - target, axis=1)
    rms = float(np.sqrt(np.mean(residuals**2)))
    if mode == "rigid":
        known["angle_sigma_deg"] = math.degrees(rms / float(singular[1]))
    angle = _angle_deg(rotation)
    known.update(
        rotation_deg=angle,
        translation_m=[float(v) for v in shift],
        residual_rms_m=rms,
        residuals_m={n: float(r) for n, r in zip(names, residuals, strict=True)},
    )
    if not SCALE_BOUNDS[0] <= scale <= SCALE_BOUNDS[1]:
        return refused(
            f"the reference's spread is {scale:.2f} times this solve's: units or cameras disagree",
            **known,
        )
    if angle > MAX_ROTATION_DEG:
        return refused(
            f"a {angle:.0f}° rotation is implausible for the same room (over "
            f"{MAX_ROTATION_DEG:.0f}°): check the cameras' identities",
            **known,
        )
    if rms > MAX_RESIDUAL_M:
        return refused(
            f"the reference does not match this rig: {100.0 * rms:.0f} cm RMS residual (over "
            f"{100.0 * MAX_RESIDUAL_M:.0f} cm), another room or a camera moved",
            **known,
        )
    # The export world is x_exp = k M x_solver: the same change, in the solver's frame.
    unit = board_unit_mm(board) / 1000.0
    transform = np.eye(4)
    transform[:3, :3] = BASIS.T @ rotation @ BASIS
    transform[:3, 3] = BASIS.T @ shift / unit
    return Alignment(AlignmentReport(mode, None, **known), transform)


def reference_payload(reference: Reference) -> dict[str, Any]:
    return {
        "name": reference.name,
        "format": reference.format,
        "cameras": [
            {"port": c.port, "device_path": c.device_path, "centre_m": c.centre_m}
            for c in reference.cameras
        ],
    }


def reference_from_payload(payload: dict[str, Any]) -> Reference:
    return Reference(
        str(payload["name"]),
        str(payload["format"]),
        [
            ReferenceCamera(int(c["port"]), c.get("device_path"), [float(v) for v in c["centre_m"]])
            for c in payload["cameras"]
        ],
    )
