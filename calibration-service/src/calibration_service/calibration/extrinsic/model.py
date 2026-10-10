"""The extrinsic solve's data and its shared geometry helpers (ADR-0023)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.calibration.intrinsic import _cv_charuco_board
from calibration_service.models.board import BoardType, CalibrationBoard

# A pair needs this many common corners in a group for it to count as a shared
# board view (Caliscope legacy_stereocal min points; also the DLT/PnP floor). A
# single-ArUco-marker board only ever yields its 4 corners, which is enough for
# the planar (homography-path) pose inside stereoCalibrate — see _min_corners().
_MIN_COMMON_CORNERS = 6


_MIN_CORNERS_SINGLE_MARKER = 4


@dataclass(frozen=True)
class CameraModel:
    """Per-camera solver inputs: intrinsics at the RECORDING (native) resolution."""

    name: str
    matrix: NDArray[np.float64]  # 3x3 K
    distortions: NDArray[np.float64]  # classic 5 coefficients [k1,k2,p1,p2,k3] (ADR-0032)


@dataclass(frozen=True)
class GroupDetection:
    """One camera's board detection inside one synchronized group (ids sorted)."""

    ids: NDArray[np.int32]  # (N,) charuco corner ids, ascending
    corners_px: NDArray[np.float64]  # (N, 2) pixel coords (native res)
    corners_norm: NDArray[np.float64]  # (N, 2) undistorted normalized coords
    # Laplacian variance over the board ROI (BoardDetection.sharpness): drives the
    # quality-based group selection (ADR-0033). 0.0 in synthetic tests.
    sharpness: float = 0.0


@dataclass(frozen=True)
class PairEstimate:
    """Primary -> secondary transform estimated by stereoCalibrate."""

    rotation: NDArray[np.float64]  # 3x3
    translation: NDArray[np.float64]  # (3,)
    error: float  # stereoCalibrate RMSE (normalized units)


@dataclass(frozen=True)
class ExtrinsicResult:
    """Solved array: per-camera world->cam pose + quality (camera-array-config)."""

    cameras: list[str]
    rotations: dict[str, list[float]]  # Rodrigues 3-vec per camera
    translations: dict[str, list[float]]  # board-square units
    per_camera_error: dict[str, float]  # pixel RMSE after BA
    error: float  # overall pixel RMSE
    pair_errors: dict[str, float]  # "cam_a|cam_b" -> stereoCalibrate RMSE
    group_count: int  # synchronized groups used
    point_count: int  # triangulated 3D points in the BA
    # 3D review scene data (spec 3d-extrinsic-review): the refined corner cloud with
    # its group index (scrub), and per group the board's 4 outline corners in world
    # coords (Kabsch fit; None when too few points). Corner order (b-l, b-r, t-r,
    # t-l in board frame) lets the webapp derive the board's local xyz triad.
    points: list[list[float]] = field(default_factory=list)
    point_groups: list[int] = field(default_factory=list)
    # Bundle-adjustment diagnostics (ADR-0036 observability): `ba_converged` is
    # False when scipy hit the _BA_MAX_NFEV ceiling instead of a tolerance — the
    # poses are then the best-so-far, NOT a converged optimum, and the webapp
    # warns. `observations_used`/`_total` report the Minimize outlier filter
    # (a full solve uses them all).
    ba_converged: bool = True
    ba_nfev: int = 0
    observations_used: int = 0
    observations_total: int = 0
    # Group whose board received the operator's "set frame" gesture (ADR-0026):
    # shown as a marker on the review scrubber. None until the gesture; a fresh
    # solve resets it (new world), rotate/minimize preserve it (same world).
    framed_group: int | None = None
    board_quads: list[list[list[float]] | None] = field(default_factory=list)
    # Observations per camera behind the error fields (ADR-0042): the exact
    # weights needed to re-aggregate the overall RMSE when per-camera errors are
    # rescaled. Empty on payloads persisted before the field existed.
    per_camera_observations: dict[str, int] = field(default_factory=dict)
    # RMS deviation (mm) of the reconstructed corner pairs from the physical
    # board (ADR-0044): the reprojection-INDEPENDENT quality judge the operator
    # reads next to the RMSE. 0.0 when no group has two triangulated corners.
    rigidity_mm: float = 0.0
    # Single-marker views the border refinement dropped, per camera and by reason, out
    # of the views it ran on per camera (ADR-0052, ADR-0036 observability). Empty for
    # ChArUco, and on older payloads.
    border_refusals: dict[str, dict[str, int]] = field(default_factory=dict)
    border_attempts: dict[str, int] = field(default_factory=dict)
    # Detected groups the motion gate dropped (ADR-0056): the board moved more than
    # the gate between the captures of their members. 0 with the gate off, on a
    # solve that had to fall back without it, and on older payloads.
    moving_groups: int = 0
    # The reference calibration the world was re-aligned on (ADR-0061): its name, the
    # mode, the matching key, rotation, translation and residual. None until that
    # gesture; a rotate or a framing drops it, a Minimize keeps it (same world), a
    # fresh solve has none.
    alignment: dict[str, Any] | None = None

    def scaled_errors(self, factors: dict[str, float]) -> ExtrinsicResult:
        """Express the pixel-error fields at each camera's OUTPUT resolution.

        The solver works and reports at the native recording resolution; the
        operator-facing contract is native x resize_factor (ADR-0015, extended
        to extrinsics by ADR-0042). Rescaling a pinhole model is exact: a pixel
        residual scales linearly with resolution, so each camera's RMSE scales
        by its own factor and the overall RMSE is re-aggregated from the
        per-camera terms weighted by observation counts. Legacy payloads without
        counts fall back to ``error x factor``, exact when factors are uniform
        (every rig so far). Geometry, pair errors (normalized, dimensionless)
        and diagnostics are untouched.
        """
        per_camera = {
            name: error * factors.get(name, 1.0) for name, error in self.per_camera_error.items()
        }
        counts = self.per_camera_observations
        total = sum(counts.get(name, 0) for name in per_camera)
        if total > 0:
            overall = float(
                np.sqrt(
                    sum(counts.get(name, 0) * per_camera[name] ** 2 for name in per_camera) / total
                )
            )
        else:
            uniform = {round(f, 12) for f in factors.values()} or {1.0}
            fallback = (
                next(iter(uniform)) if len(uniform) == 1 else sum(factors.values()) / len(factors)
            )
            overall = self.error * float(fallback)
        return replace(self, error=overall, per_camera_error=per_camera)


def _natural_key(name: str) -> tuple[str, int]:
    """Sort key ordering ``cam_2`` before ``cam_10`` (plain sorted() does not)."""
    stem, _, number = name.rpartition("_")
    return (stem, int(number)) if number.isdecimal() else (name, -1)


def _transform(
    rotation: NDArray[np.float64], translation: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Build the 4x4 [R|t; 0 1] transform (x_b = R @ x_a + t)."""
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation.reshape(3)
    return matrix


def _pose(rvec: Any, tvec: Any) -> NDArray[np.float64]:
    """The 4x4 pose of a Rodrigues vector and a translation."""
    rotation, _ = cv2.Rodrigues(np.asarray(rvec, np.float64))
    return _transform(np.asarray(rotation, np.float64), np.asarray(tvec, np.float64))


def _rvec_t(pose: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """The Rodrigues vector (3,) and the translation (3,) of a 4x4 pose."""
    rvec, _ = cv2.Rodrigues(pose[:3, :3])
    return np.asarray(rvec, np.float64).reshape(3), np.asarray(pose[:3, 3], np.float64)


def _result_poses(result: ExtrinsicResult) -> dict[str, NDArray[np.float64]]:
    """Each camera's 4x4 world -> camera pose, from a result's Rodrigues and t."""
    return {n: _pose(result.rotations[n], result.translations[n]) for n in result.cameras}


def _pose_lists(
    poses: dict[str, NDArray[np.float64]],
) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Poses as a result stores them: Rodrigues vectors and translations, per camera."""
    rotations: dict[str, list[float]] = {}
    translations: dict[str, list[float]] = {}
    for name, pose in poses.items():
        rvec, tvec = _rvec_t(pose)
        rotations[name] = [float(v) for v in rvec]
        translations[name] = [float(v) for v in tvec]
    return rotations, translations


def _common_ids(det_a: GroupDetection, det_b: GroupDetection) -> NDArray[np.int32]:
    """The corner (or marker-corner) ids two cameras saw in one group, sorted."""
    return np.asarray(np.intersect1d(det_a.ids, det_b.ids), np.int32)


def board_object_points(board: CalibrationBoard) -> NDArray[np.float64]:
    """3D reference points of the extrinsic target, indexed by detection corner id.

    ChArUco: the chessboard corners (id = charuco corner id; unit = square side).
    Single ArUco marker: its 4 canonical corners TL,TR,BR,BL (ids remapped 0..3 by
    the compute detection; unit = MARKER side) — matching the detector's canonical
    square and cv2's stable corner order across views.
    """
    if board.board_type is BoardType.CHARUCO:
        return np.asarray(_cv_charuco_board(board).getChessboardCorners(), np.float64)
    return np.array(
        [[-0.5, 0.5, 0.0], [0.5, 0.5, 0.0], [0.5, -0.5, 0.0], [-0.5, -0.5, 0.0]], np.float64
    )


def board_unit_mm(board: CalibrationBoard) -> float:
    """Physical size (mm) of one board unit: square side (ChArUco) or marker side."""
    if board.board_type is BoardType.CHARUCO:
        return board.square_size_mm
    return board.marker_size_mm


def _min_corners(board: CalibrationBoard) -> int:
    """Minimum common corners for a usable shared view (4 for a single marker)."""
    return (
        _MIN_COMMON_CORNERS
        if board.board_type is BoardType.CHARUCO
        else (_MIN_CORNERS_SINGLE_MARKER)
    )


@dataclass(frozen=True)
class BAInputs:
    """Persisted bundle-adjustment observations (Minimize re-runs without redetecting)."""

    obs_camera: list[int]
    obs_point: list[int]
    obs_norm: list[list[float]]
    obs_px: list[list[float]]
    point_corner: list[int]
