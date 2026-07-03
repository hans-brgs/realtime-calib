"""Extrinsic multi-camera calibration (ADR-0023, Caliscope-grounded).

Pipeline: synchronized detection groups -> pairwise ``cv2.stereoCalibrate`` on
**undistorted normalized** points (K=I, D=0, ``CALIB_FIX_INTRINSIC``) -> transform
graph with bridge-filling -> poses chained from the **anchor** (camera index 0,
identity — ADR-0012) -> **DLT triangulation** (batched SVD over all observing
cameras) -> **bundle adjustment** (``scipy.optimize.least_squares``, trf, sparse
Jacobian) refining non-anchor poses + 3D points jointly.

Two deliberate conventions (ADR-0023):
- Poses map **world (anchor frame) coords -> camera coords**: ``x_cam = R x_w + t``
  — directly usable by ``cv2.projectPoints``. Pairwise transforms map primary ->
  secondary the same way (``x_b = R_ab x_a + t_ab``), composing as
  ``T_ac = T_xc @ T_ax`` (Caliscope ``StereoPair.link``).
- The **anchor is FIXED in the BA** (its 6 params are excluded from the vector).
  Caliscope leaves every camera free (unconstrained 6-DoF gauge) and relies on the
  solver not drifting; excluding the anchor removes that gauge freedom, conditions
  the Jacobian, and guarantees ``anchor == identity`` by construction. One gauge
  mode remains — **global scale** (scaling all points + translations leaves every
  normalized projection invariant, and the anchor's t=0 is scale-free) — which no
  BA can observe; the scale is pinned by the stereoCalibrate init, whose object
  points are the physical board, and only wobbles within convergence tolerance.

Units: board squares (the ChArUco board is built with ``squareLength=1.0``);
translations stay in squares until the export scales by ``square_size_mm``
([[camera-array-config]]).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares  # type: ignore[import-untyped]
from scipy.sparse import lil_matrix  # type: ignore[import-untyped]

from calibration_service.calibration.intrinsic import _cv_charuco_board
from calibration_service.detection import BoardDetector
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.recording import read_timestamps
from calibration_service.synchronization import SyncFrame, SyncGroup

logger = logging.getLogger(__name__)

# A pair needs this many common corners in a group for it to count as a shared
# board view (Caliscope legacy_stereocal min points; also the DLT/PnP floor). A
# single-ArUco-marker board only ever yields its 4 corners, which is enough for
# the planar (homography-path) pose inside stereoCalibrate — see _min_corners().
_MIN_COMMON_CORNERS = 6
_MIN_CORNERS_SINGLE_MARKER = 4
# Shared boards actually fed to stereoCalibrate per pair, picked for temporal
# diversity (Caliscope boards_sampled).
_BOARDS_SAMPLED = 10
# Minimum shared groups for a pair to be estimated at all (below this the
# geometry is too weak; the co-visibility gauge tells the operator live).
_DEFAULT_MIN_SHARED = 5
# Detection budget over the sweep: groups are subsampled evenly to this cap
# (detection dominates compute cost, like the intrinsic _MAX_DETECT_FRAMES).
# Single-marker detection is ~10x cheaper than ChArUco AND each view carries only
# 4 corners, so markers get a much larger budget (more views = the redundancy).
_MAX_DETECT_GROUPS = 80
_MAX_DETECT_GROUPS_SINGLE_MARKER = 240
# Shared boards fed to stereoCalibrate per pair (see _BOARDS_SAMPLED); with only
# 4 corners per marker view, average over more views.
_BOARDS_SAMPLED_SINGLE_MARKER = 25
# stereoCalibrate refinement on normalized points (Caliscope criteria).
_STEREO_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
# Bundle-adjustment settings (Caliscope capture_volume least_squares kwargs), with
# one deliberate deviation: a HUBER loss instead of linear. Caliscope's 42-corner
# ChArUco views drown an occasional misdetection; a 4-corner marker view does not,
# and one bad frame poisons a linear BA (observed 17.7 px on the real rig). The
# scale is in normalized-image units: ~2 px at a ~1350 px focal.
_BA_FTOL = 1e-8
_BA_MAX_NFEV = 1000
_BA_HUBER_SCALE = 0.0015
# Minimize = Caliscope's quality loop (filter_point_estimates -> optimize): drop
# the worst observations by CURRENT pixel residual, then re-fit. Product fraction
# is 2.5% in both Caliscope eras (legacy FILTERED_FRACTION, current
# filter_by_percentile_error(2.5)); min-per-camera floor from current Caliscope.
_REFINE_FILTER_FRACTION = 0.025
_REFINE_MIN_PER_CAMERA = 10


@dataclass(frozen=True)
class CameraModel:
    """Per-camera solver inputs: intrinsics at the RECORDING (native) resolution."""

    name: str
    matrix: NDArray[np.float64]  # 3x3 K
    distortions: NDArray[np.float64]  # rational-model coefficients


@dataclass(frozen=True)
class GroupDetection:
    """One camera's board detection inside one synchronized group (ids sorted)."""

    ids: NDArray[np.int32]  # (N,) charuco corner ids, ascending
    corners_px: NDArray[np.float64]  # (N, 2) pixel coords (native res)
    corners_norm: NDArray[np.float64]  # (N, 2) undistorted normalized coords


@dataclass(frozen=True)
class PairEstimate:
    """Primary -> secondary transform estimated by stereoCalibrate."""

    rotation: NDArray[np.float64]  # 3x3
    translation: NDArray[np.float64]  # (3,)
    error: float  # stereoCalibrate RMSE (normalized units)
    shared_groups: int


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
    board_quads: list[list[list[float]] | None] = field(default_factory=list)


def _transform(
    rotation: NDArray[np.float64], translation: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Build the 4x4 [R|t; 0 1] transform (x_b = R @ x_a + t)."""
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation.reshape(3)
    return matrix


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
    return _MIN_COMMON_CORNERS if board.board_type is BoardType.CHARUCO else (
        _MIN_CORNERS_SINGLE_MARKER
    )


def derive_sweep_window(directory: Path, names: list[str]) -> float:
    """Sync window derived from the RECORDED cadence, not the configured fps.

    The capture loop's effective write rate can be well below the camera fps
    (detection/encode contention) — observed ~18 fps for a 30 fps config. A window
    below one REAL frame interval keeps pairing unambiguous (ADR-0007 intent):
    0.95 x the slowest camera's median inter-frame delta, clamped to sane bounds.
    """
    medians: list[float] = []
    for name in names:
        path = directory / f"{name}.timestamps"
        if not path.is_file():
            continue  # camera missing from the sweep: sync simply excludes it
        stamps = read_timestamps(path)
        if len(stamps) >= 2:
            deltas = np.diff(np.asarray(stamps, np.float64))
            medians.append(float(np.median(deltas)))
    if not medians:
        raise ValueError(f"no recorded timestamps under {directory}")
    return float(np.clip(0.95 * max(medians), 0.02, 0.25))


def _diverse_group_indices(candidates: list[tuple[int, int]], cap: int) -> list[int]:
    """Pick up to ``cap`` group indices spread over time, best corner count per bin.

    ``candidates`` are (group_index, common_corner_count) in temporal order
    (Caliscope ``_select_diverse_boards``: temporal binning, count as quality).
    """
    if len(candidates) <= cap:
        return [index for index, _ in candidates]
    bins: dict[int, tuple[int, int]] = {}
    span = len(candidates)
    for position, (index, count) in enumerate(candidates):
        bin_id = min(cap - 1, position * cap // span)
        best = bins.get(bin_id)
        if best is None or count > best[1]:
            bins[bin_id] = (index, count)
    return sorted(index for index, _ in bins.values())


def stereo_pairwise(
    groups: list[dict[str, GroupDetection]],
    board: CalibrationBoard,
    *,
    min_shared: int = _DEFAULT_MIN_SHARED,
) -> dict[tuple[str, str], PairEstimate]:
    """Estimate the primary->secondary transform of every co-visible camera pair.

    For each pair, shared boards (enough common ids in a group — 6 for ChArUco,
    the 4 marker corners for a single-ArUco target) are collected, subsampled for
    temporal diversity, and fed to ``cv2.stereoCalibrate`` in **normalized
    coordinates** (identity K, zero distortion, ``CALIB_FIX_INTRINSIC`` — only
    R|T are optimised, Caliscope).
    """
    chess = board_object_points(board)
    min_corners = _min_corners(board)
    names = sorted({name for group in groups for name in group})
    pairs: dict[tuple[str, str], PairEstimate] = {}
    for i, cam_a in enumerate(names):
        for cam_b in names[i + 1 :]:
            shared: list[tuple[int, int]] = []  # (group index, common corner count)
            for index, group in enumerate(groups):
                det_a, det_b = group.get(cam_a), group.get(cam_b)
                if det_a is None or det_b is None:
                    continue
                common = np.intersect1d(det_a.ids, det_b.ids)
                if len(common) >= min_corners:
                    shared.append((index, len(common)))
            if len(shared) < min_shared:
                continue

            boards_cap = (
                _BOARDS_SAMPLED
                if board.board_type is BoardType.CHARUCO
                else _BOARDS_SAMPLED_SINGLE_MARKER
            )
            object_points: list[NDArray[np.float32]] = []
            points_a: list[NDArray[np.float32]] = []
            points_b: list[NDArray[np.float32]] = []
            for index in _diverse_group_indices(shared, boards_cap):
                det_a, det_b = groups[index][cam_a], groups[index][cam_b]
                common = np.intersect1d(det_a.ids, det_b.ids)
                sel_a = np.searchsorted(det_a.ids, common)
                sel_b = np.searchsorted(det_b.ids, common)
                object_points.append(chess[common].astype(np.float32))
                points_a.append(det_a.corners_norm[sel_a].astype(np.float32))
                points_b.append(det_b.corners_norm[sel_b].astype(np.float32))

            identity = np.eye(3)
            zeros = np.zeros(5)
            result = cv2.stereoCalibrate(  # type: ignore[call-overload]
                object_points,
                points_a,
                points_b,
                identity,
                zeros,
                identity,
                zeros,
                None,  # imageSize: unused under CALIB_FIX_INTRINSIC (normalized pts)
                criteria=_STEREO_CRITERIA,
                flags=cv2.CALIB_FIX_INTRINSIC,
            )
            rmse, _, _, _, _, rotation, translation = result[:7]
            pairs[(cam_a, cam_b)] = PairEstimate(
                rotation=np.asarray(rotation, np.float64),
                translation=np.asarray(translation, np.float64).reshape(3),
                error=float(rmse),
                shared_groups=len(shared),
            )
            logger.info(
                "pair %s-%s: %d shared groups, stereo RMSE %.4f",
                cam_a,
                cam_b,
                len(shared),
                float(rmse),
            )
    return pairs


def chain_from_anchor(
    pairs: dict[tuple[str, str], PairEstimate],
    cameras: list[str],
    anchor: str,
) -> dict[str, NDArray[np.float64]]:
    """Chain every camera's world->cam 4x4 pose from the anchor (ADR-0012).

    Builds a bidirectional transform graph from the pairwise estimates and takes
    the lowest-cumulative-error PATH from the anchor to each camera (Dijkstra) —
    a strict generalisation of Caliscope's bridge-filling: bridges compete with
    poor direct estimates instead of only replacing missing ones. The anchor is
    identity. Raises ``ValueError`` when a camera is unreachable from the anchor
    (no joint board views — guard-rail ADR-0012).
    """
    edges: dict[str, list[tuple[str, NDArray[np.float64], float]]] = {c: [] for c in cameras}
    for (cam_a, cam_b), pair in pairs.items():
        forward = _transform(pair.rotation, pair.translation)
        edges[cam_a].append((cam_b, forward, pair.error))
        edges[cam_b].append((cam_a, np.linalg.inv(forward), pair.error))

    # Lowest-cumulative-error path from the anchor to EVERY camera (Dijkstra on the
    # pair graph). Unlike fill-missing-pairs-only bridging, this also routes AROUND
    # a poor direct estimate: on the real rig cam_0|cam_1 measured 65x worse than
    # its neighbours, and the 3-hop route beat the direct pair by an order of
    # magnitude — the direct edge must compete with bridges, not shadow them.
    cost: dict[str, float] = {anchor: 0.0}
    poses: dict[str, NDArray[np.float64]] = {anchor: np.eye(4)}
    visited: set[str] = set()
    while True:
        current = min(
            (c for c in cost if c not in visited), key=lambda c: cost[c], default=None
        )
        if current is None:
            break
        visited.add(current)
        for neighbour, forward, error in edges.get(current, []):
            candidate = cost[current] + error
            if neighbour not in cost or candidate < cost[neighbour]:
                cost[neighbour] = candidate
                poses[neighbour] = forward @ poses[current]

    unreachable = [c for c in cameras if c not in poses]
    if unreachable:
        raise ValueError(
            "cameras not co-visible with the anchor (need joint board views): "
            + ", ".join(sorted(unreachable))
        )
    return poses


@dataclass(frozen=True)
class Triangulation:
    """DLT-triangulated corner cloud + the flat BA observation records."""

    points3d: NDArray[np.float64]  # (P, 3)
    point_group: NDArray[np.intp]  # (P,) synchronized-group index of each point
    point_corner: NDArray[np.int32]  # (P,) charuco corner id of each point
    obs_camera: NDArray[np.intp]  # (O,) index into camera_order
    obs_point: NDArray[np.intp]  # (O,) index into points3d
    obs_norm: NDArray[np.float64]  # (O, 2) undistorted normalized observations
    obs_px: NDArray[np.float64]  # (O, 2) pixel observations (error reporting)
    camera_order: list[str]


def triangulate_groups(
    groups: list[dict[str, GroupDetection]],
    poses: dict[str, NDArray[np.float64]],
) -> Triangulation:
    """Triangulate every corner seen by >= 2 cameras; build the BA observation set.

    DLT with **all** observing rays: per point, stack ``x*P2 - P0`` / ``y*P2 - P1``
    rows (P = the camera's normalized [R|t]) into a 2Nx4 system and take the SVD
    null-space — batched per camera-set like Caliscope ``point_data``.
    """
    camera_order = sorted(poses)
    camera_index = {name: i for i, name in enumerate(camera_order)}
    projections = {name: pose[:3, :] for name, pose in poses.items()}

    point_ids: dict[tuple[int, int], int] = {}
    point_obs: list[list[tuple[int, float, float, float, float]]] = []
    point_keys: list[tuple[int, int]] = []
    for group_idx, group in enumerate(groups):
        for name, detection in group.items():
            if name not in camera_index:
                continue
            cam = camera_index[name]
            for row in range(len(detection.ids)):
                key = (group_idx, int(detection.ids[row]))
                point = point_ids.get(key)
                if point is None:
                    point = len(point_obs)
                    point_ids[key] = point
                    point_obs.append([])
                    point_keys.append(key)
                nx, ny = detection.corners_norm[row]
                px, py = detection.corners_px[row]
                point_obs[point].append((cam, float(nx), float(ny), float(px), float(py)))

    kept: list[list[tuple[int, float, float, float, float]]] = []
    kept_keys: list[tuple[int, int]] = []
    for obs, key in zip(point_obs, point_keys, strict=True):
        if len({cam for cam, *_ in obs}) >= 2:
            kept.append(obs)
            kept_keys.append(key)
    if not kept:
        raise ValueError("no corner is seen by >= 2 cameras; the sweep lacks joint views")

    by_camset: dict[tuple[int, ...], list[int]] = {}
    for point, obs in enumerate(kept):
        camset = tuple(sorted({cam for cam, *_ in obs}))
        by_camset.setdefault(camset, []).append(point)

    points3d = np.zeros((len(kept), 3))
    for camset, members in by_camset.items():
        stack = np.zeros((len(members), 2 * len(camset), 4))
        for slot, point in enumerate(members):
            per_cam = {cam: (nx, ny) for cam, nx, ny, _, _ in kept[point]}
            for j, cam in enumerate(camset):
                projection = projections[camera_order[cam]]
                nx, ny = per_cam[cam]
                stack[slot, 2 * j] = nx * projection[2] - projection[0]
                stack[slot, 2 * j + 1] = ny * projection[2] - projection[1]
        _, _, vh = np.linalg.svd(stack)
        homogeneous = vh[:, -1, :]
        points3d[members] = homogeneous[:, :3] / homogeneous[:, 3:4]

    obs_camera: list[int] = []
    obs_point: list[int] = []
    obs_norm: list[tuple[float, float]] = []
    obs_px: list[tuple[float, float]] = []
    for point, obs in enumerate(kept):
        for cam, nx, ny, px, py in obs:
            obs_camera.append(cam)
            obs_point.append(point)
            obs_norm.append((nx, ny))
            obs_px.append((px, py))
    return Triangulation(
        points3d=points3d,
        point_group=np.asarray([key[0] for key in kept_keys], np.intp),
        point_corner=np.asarray([key[1] for key in kept_keys], np.int32),
        obs_camera=np.asarray(obs_camera, np.intp),
        obs_point=np.asarray(obs_point, np.intp),
        obs_norm=np.asarray(obs_norm, np.float64),
        obs_px=np.asarray(obs_px, np.float64),
        camera_order=camera_order,
    )


def bundle_adjust(
    camera_order: list[str],
    poses: dict[str, NDArray[np.float64]],
    points3d: NDArray[np.float64],
    obs_camera: NDArray[np.intp],
    obs_point: NDArray[np.intp],
    obs_norm: NDArray[np.float64],
    anchor: str,
) -> tuple[dict[str, NDArray[np.float64]], NDArray[np.float64]]:
    """Jointly refine non-anchor poses + 3D points on normalized reprojection error.

    Parameter vector = ``[rvec|tvec per NON-anchor camera, then xyz per point]``;
    the anchor stays identity (gauge fixed, ADR-0023). Sparse Jacobian: each
    residual row touches its camera's 6 params (unless anchor) + its point's 3.
    Residuals in normalized coords (undistorted upstream), projected with K=I —
    Caliscope's ``use_normalized`` mode (Triggs et al. conditioning).
    """
    free = [name for name in camera_order if name != anchor]
    free_slot = {name: i for i, name in enumerate(free)}
    n_cam_params = 6 * len(free)
    n_obs = len(obs_camera)

    x0 = np.zeros(n_cam_params + 3 * len(points3d))
    for name, slot in free_slot.items():
        pose = poses[name]
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        x0[6 * slot : 6 * slot + 3] = rvec.reshape(3)
        x0[6 * slot + 3 : 6 * slot + 6] = pose[:3, 3]
    x0[n_cam_params:] = points3d.ravel()

    # The anchor is held at its CURRENT pose (identity right after chaining, but a
    # reorientation may have moved the world frame — Minimize must not undo it).
    anchor_rvec_m, _ = cv2.Rodrigues(poses[anchor][:3, :3])
    anchor_rvec = np.asarray(anchor_rvec_m, np.float64).reshape(3)
    anchor_tvec = np.asarray(poses[anchor][:3, 3], np.float64)

    masks = [obs_camera == c for c in range(len(camera_order))]
    identity_k = np.eye(3)

    def residuals(params: NDArray[np.float64]) -> NDArray[np.float64]:
        points = params[n_cam_params:].reshape(-1, 3)
        projected = np.empty_like(obs_norm)
        for cam, name in enumerate(camera_order):
            mask = masks[cam]
            if not bool(mask.any()):
                continue
            if name == anchor:
                rvec = anchor_rvec
                tvec = anchor_tvec
            else:
                slot = free_slot[name]
                rvec = params[6 * slot : 6 * slot + 3]
                tvec = params[6 * slot + 3 : 6 * slot + 6]
            image_points, _ = cv2.projectPoints(
                points[obs_point[mask]], rvec, tvec, identity_k, None
            )
            projected[mask] = image_points.reshape(-1, 2)
        return np.asarray((projected - obs_norm).ravel(), np.float64)

    sparsity = lil_matrix((2 * n_obs, len(x0)), dtype=int)
    rows = np.arange(n_obs)
    for cam, name in enumerate(camera_order):
        if name == anchor:
            continue
        slot = free_slot[name]
        selected = rows[masks[cam]]
        for k in range(6):
            sparsity[2 * selected, 6 * slot + k] = 1
            sparsity[2 * selected + 1, 6 * slot + k] = 1
    for k in range(3):
        sparsity[2 * rows, n_cam_params + 3 * obs_point + k] = 1
        sparsity[2 * rows + 1, n_cam_params + 3 * obs_point + k] = 1

    # Two-stage solve: a linear pass first (full gradients converge the geometry
    # from the chained init), then a Huber pass from that solution so residual
    # outliers — e.g. a misdetected 4-corner marker view — stop steering the fit.
    # Huber alone stalls from a coarse init (everything starts beyond f_scale).
    common = {
        "jac_sparsity": sparsity,
        "method": "trf",
        "x_scale": "jac",
        "ftol": _BA_FTOL,
        "max_nfev": _BA_MAX_NFEV,
    }
    first = least_squares(residuals, x0, loss="linear", **common)
    result = least_squares(
        residuals, np.asarray(first.x, np.float64), loss="huber", f_scale=_BA_HUBER_SCALE, **common
    )
    solution = np.asarray(result.x, np.float64)
    logger.info("bundle adjustment: cost %.6f -> %.6f", float(result.cost), float(result.cost))

    solved: dict[str, NDArray[np.float64]] = {anchor: poses[anchor].copy()}
    for name, slot in free_slot.items():
        rmat, _ = cv2.Rodrigues(solution[6 * slot : 6 * slot + 3])
        solved[name] = _transform(
            np.asarray(rmat, np.float64), solution[6 * slot + 3 : 6 * slot + 6]
        )
    return solved, solution[n_cam_params:].reshape(-1, 3)


def _observation_residuals_px(
    camera_order: list[str],
    poses: dict[str, NDArray[np.float64]],
    points3d: NDArray[np.float64],
    obs_camera: NDArray[np.intp],
    obs_point: NDArray[np.intp],
    obs_px: NDArray[np.float64],
    models: dict[str, CameraModel],
) -> NDArray[np.float64]:
    """Per-observation euclidean reprojection error in PIXELS (full K + distortion)."""
    errors = np.zeros(len(obs_camera), np.float64)
    for cam, name in enumerate(camera_order):
        mask = obs_camera == cam
        if not bool(mask.any()):
            continue
        pose = poses[name]
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        model = models[name]
        projected, _ = cv2.projectPoints(
            points3d[obs_point[mask]], rvec, pose[:3, 3], model.matrix, model.distortions
        )
        diff = projected.reshape(-1, 2) - obs_px[mask]
        errors[mask] = np.linalg.norm(diff, axis=1)
    return errors


def pixel_errors(
    camera_order: list[str],
    poses: dict[str, NDArray[np.float64]],
    points3d: NDArray[np.float64],
    obs_camera: NDArray[np.intp],
    obs_point: NDArray[np.intp],
    obs_px: NDArray[np.float64],
    models: dict[str, CameraModel],
) -> tuple[dict[str, float], float]:
    """Per-camera + overall RMSE in PIXELS (full K + distortion) for reporting."""
    errors = _observation_residuals_px(
        camera_order, poses, points3d, obs_camera, obs_point, obs_px, models
    )
    per_camera: dict[str, float] = {}
    for cam, name in enumerate(camera_order):
        mask = obs_camera == cam
        per_camera[name] = float(np.sqrt((errors[mask] ** 2).mean())) if bool(mask.any()) else 0.0
    overall = float(np.sqrt((errors**2).mean())) if len(errors) else 0.0
    return per_camera, overall


def _filter_observations(
    obs_camera: NDArray[np.intp],
    obs_point: NDArray[np.intp],
    residuals: NDArray[np.float64],
    fraction: float,
) -> NDArray[np.bool_]:
    """Keep-mask dropping the worst ``fraction`` of observations by residual.

    Caliscope's filter: global percentile over euclidean pixel errors, with two
    restore guards — every 3D point keeps >= 2 observations (stays constrained in
    the BA; legacy Caliscope deleted such points instead, but our scene arrays are
    index-aligned with groups, so points are kept), and every camera keeps
    >= _REFINE_MIN_PER_CAMERA observations (current Caliscope ``min_per_camera``).
    Restored slots are the lowest-residual trimmed ones.
    """
    keep = residuals <= np.percentile(residuals, 100.0 * (1.0 - fraction))
    for point in np.unique(obs_point[~keep]):
        selected = np.flatnonzero(obs_point == point)
        missing = 2 - int(keep[selected].sum())
        if missing <= 0:
            continue
        dropped = selected[~keep[selected]]
        keep[dropped[np.argsort(residuals[dropped])][:missing]] = True
    for cam in np.unique(obs_camera[~keep]):
        selected = np.flatnonzero(obs_camera == cam)
        floor = min(_REFINE_MIN_PER_CAMERA, len(selected))
        missing = floor - int(keep[selected].sum())
        if missing <= 0:
            continue
        dropped = selected[~keep[selected]]
        keep[dropped[np.argsort(residuals[dropped])][:missing]] = True
    return keep


def _kabsch(source: NDArray[np.float64], target: NDArray[np.float64]) -> NDArray[np.float64]:
    """4x4 rigid transform (proper rotation) best mapping source -> target points.

    Classic orthogonal Procrustes: SVD of the cross-covariance with a determinant
    correction so planar point sets (the board) still yield a proper rotation.
    """
    source_center = source.mean(axis=0)
    target_center = target.mean(axis=0)
    covariance = (source - source_center).T @ (target - target_center)
    u, _, vt = np.linalg.svd(covariance)
    sign = float(np.sign(np.linalg.det(vt.T @ u.T))) or 1.0
    rotation = vt.T @ np.diag([1.0, 1.0, sign]) @ u.T
    translation = target_center - rotation @ source_center
    return _transform(rotation, translation)


def _group_board_quads(
    point_group: NDArray[np.intp],
    point_corner: NDArray[np.int32],
    points3d: NDArray[np.float64],
    chess: NDArray[np.float64],
    group_count: int,
    min_corners: int = _MIN_COMMON_CORNERS,
) -> list[list[list[float]] | None]:
    """Per group, the board's 4 outline corners in world coords (Kabsch fit).

    Corner order: board-frame (min,min) -> (max,min) -> (max,max) -> (min,max) —
    the webapp derives the board's local xyz triad from it (x = c0->c1, y = c0->c3,
    z = x cross y). ``None`` when a group has too few triangulated corners.
    """
    low, high = chess.min(axis=0), chess.max(axis=0)
    outline = np.array(
        [
            [low[0], low[1], 0.0],
            [high[0], low[1], 0.0],
            [high[0], high[1], 0.0],
            [low[0], high[1], 0.0],
        ]
    )
    quads: list[list[list[float]] | None] = []
    for group in range(group_count):
        mask = point_group == group
        if int(mask.sum()) < min_corners:
            quads.append(None)
            continue
        board_points = chess[point_corner[mask]]
        pose = _kabsch(board_points, points3d[mask])
        placed = outline @ pose[:3, :3].T + pose[:3, 3]
        quads.append([[float(v) for v in corner] for corner in placed])
    return quads


def axis_rotation_transform(axis: str, degrees: float) -> NDArray[np.float64]:
    """World-frame change G (old->new coords): rotation about the current origin.

    ``x_new = G_R @ x_old`` — the spec's ±xyz reorientation buttons compose these.
    """
    radians = np.radians(degrees)
    c, s = float(np.cos(radians)), float(np.sin(radians))
    if axis == "x":
        rotation = np.array([[1.0, 0, 0], [0, c, -s], [0, s, c]])
    elif axis == "y":
        rotation = np.array([[c, 0, s], [0, 1.0, 0], [-s, 0, c]])
    elif axis == "z":
        rotation = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])
    else:
        raise ValueError(f"unknown axis {axis!r}")
    return _transform(rotation, np.zeros(3))


def quad_origin_transform(
    quad: list[list[float]], *, at_center: bool = False
) -> NDArray[np.float64]:
    """World-frame change G placing the origin + axes on a board quad ('Set origin').

    The quad's corner order (c0 bl, c1 br, c3 tl) defines the board basis B
    (board->world); the new world IS that board frame: ``x_new = B^-1 x_old``.
    ``at_center`` anchors the origin on the quad centroid instead of c0 — the
    single-ArUco convention (cv2 places the marker frame at its CENTER), whereas
    a ChArUco board frame originates at its first chessboard corner.
    The basis is re-orthonormalised (the Kabsch quad is rigid, but guard anyway).
    """
    corners = np.asarray(quad, np.float64)
    x = corners[1] - corners[0]
    x = x / np.linalg.norm(x)
    y_raw = corners[3] - corners[0]
    y = y_raw - x * float(x @ y_raw)
    y = y / np.linalg.norm(y)
    z = np.cross(x, y)
    basis = np.column_stack([x, y, z])  # board->world rotation
    anchor = corners.mean(axis=0) if at_center else corners[0]
    g_rotation = basis.T
    g_translation = -basis.T @ anchor
    return _transform(g_rotation, g_translation)


def reorient_result(result: ExtrinsicResult, transform: NDArray[np.float64]) -> ExtrinsicResult:
    """Re-express the solved array in a new world frame (rigid G: old->new coords).

    Cameras: ``x_cam = R x_old + t`` with ``x_old = G^-1 x_new`` gives
    ``R' = R G_R^T``, ``t' = t - R' G_t``. Points/quads map as ``G_R p + G_t``.
    Reprojection errors are invariant under a rigid world change, so all quality
    fields carry over unchanged.
    """
    g_rotation = transform[:3, :3]
    g_translation = transform[:3, 3]

    rotations: dict[str, list[float]] = {}
    translations: dict[str, list[float]] = {}
    for name in result.cameras:
        r_matrix = np.asarray(cv2.Rodrigues(np.asarray(result.rotations[name]))[0], np.float64)
        t_vector = np.asarray(result.translations[name], np.float64)
        new_r = r_matrix @ g_rotation.T
        new_t = t_vector - new_r @ g_translation
        rvec, _ = cv2.Rodrigues(new_r)
        rotations[name] = [float(v) for v in np.asarray(rvec).reshape(3)]
        translations[name] = [float(v) for v in new_t]

    points = np.asarray(result.points, np.float64)
    moved_points = points @ g_rotation.T + g_translation if len(points) else points
    quads: list[list[list[float]] | None] = []
    for quad in result.board_quads:
        if quad is None:
            quads.append(None)
        else:
            moved = np.asarray(quad, np.float64) @ g_rotation.T + g_translation
            quads.append([[float(v) for v in corner] for corner in moved])

    return ExtrinsicResult(
        cameras=result.cameras,
        rotations=rotations,
        translations=translations,
        per_camera_error=result.per_camera_error,
        error=result.error,
        pair_errors=result.pair_errors,
        group_count=result.group_count,
        point_count=result.point_count,
        points=[[float(v) for v in point] for point in moved_points],
        point_groups=result.point_groups,
        board_quads=quads,
    )


@dataclass(frozen=True)
class BAInputs:
    """Persisted bundle-adjustment observations (Minimize re-runs without redetecting)."""

    obs_camera: list[int]
    obs_point: list[int]
    obs_norm: list[list[float]]
    obs_px: list[list[float]]
    point_corner: list[int]


def refine_result(
    result: ExtrinsicResult,
    ba_inputs: BAInputs,
    models: list[CameraModel],
    board: CalibrationBoard,
    anchor: str,
) -> ExtrinsicResult:
    """Filter outliers + re-run the bundle adjustment from the CURRENT result.

    The spec's 'Minimize': Caliscope's quality loop (filter_point_estimates ->
    optimize) — drop the worst _REFINE_FILTER_FRACTION of the persisted
    observations by their residual under the current fit, then re-fit and report
    the post-filter RMSE. Always starts from the FULL persisted observations, so
    repeat clicks converge instead of ratcheting data away (deviation from
    Caliscope's cumulative GUI filter: we expose one button, not a fraction knob +
    recalibrate reset). Holds the anchor at its current pose, so an operator
    reorientation (origin/±xyz) is preserved. No re-detection.
    """
    poses: dict[str, NDArray[np.float64]] = {}
    for name in result.cameras:
        rotation = np.asarray(cv2.Rodrigues(np.asarray(result.rotations[name]))[0], np.float64)
        poses[name] = _transform(rotation, np.asarray(result.translations[name], np.float64))

    obs_camera = np.asarray(ba_inputs.obs_camera, np.intp)
    obs_point = np.asarray(ba_inputs.obs_point, np.intp)
    obs_norm = np.asarray(ba_inputs.obs_norm, np.float64)
    obs_px = np.asarray(ba_inputs.obs_px, np.float64)
    points3d = np.asarray(result.points, np.float64)
    model_map = {model.name: model for model in models}

    residuals = _observation_residuals_px(
        result.cameras, poses, points3d, obs_camera, obs_point, obs_px, model_map
    )
    keep = _filter_observations(obs_camera, obs_point, residuals, _REFINE_FILTER_FRACTION)
    logger.info("minimize: %d/%d observations kept", int(keep.sum()), len(keep))

    solved, refined = bundle_adjust(
        result.cameras, poses, points3d, obs_camera[keep], obs_point[keep], obs_norm[keep], anchor
    )
    per_camera, overall = pixel_errors(
        result.cameras,
        solved,
        refined,
        obs_camera[keep],
        obs_point[keep],
        obs_px[keep],
        model_map,
    )

    rotations: dict[str, list[float]] = {}
    translations: dict[str, list[float]] = {}
    for name, pose in solved.items():
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        rotations[name] = [float(v) for v in np.asarray(rvec).reshape(3)]
        translations[name] = [float(v) for v in pose[:3, 3]]

    chess = board_object_points(board)
    return ExtrinsicResult(
        cameras=result.cameras,
        rotations=rotations,
        translations=translations,
        per_camera_error=per_camera,
        error=overall,
        pair_errors=result.pair_errors,
        group_count=result.group_count,
        point_count=len(refined),
        points=[[float(v) for v in point] for point in refined],
        point_groups=result.point_groups,
        board_quads=_group_board_quads(
            np.asarray(result.point_groups, np.intp),
            np.asarray(ba_inputs.point_corner, np.int32),
            refined,
            chess,
            result.group_count,
            min_corners=_min_corners(board),
        ),
    )


def _detect_group_frames(
    directory: Path,
    groups_frames: list[dict[str, int]],
    models: dict[str, CameraModel],
    board: CalibrationBoard,
) -> list[dict[str, GroupDetection]]:
    """Detect the board on each selected (camera, frame-index) and normalize corners.

    Single-ArUco targets: the detector reports every corner under the marker id
    (e.g. [8,8,8,8]); remap to per-CORNER ids 0..3 (cv2's TL,TR,BR,BL order is
    stable across views) so cross-camera correspondence + ``board_object_points``
    indexing work like the ChArUco path.
    """
    detector = BoardDetector(board)
    single_marker = board.board_type is not BoardType.CHARUCO
    min_corners = _min_corners(board)
    needed: dict[str, list[tuple[int, int]]] = {}
    for position, group in enumerate(groups_frames):
        for name, frame_index in group.items():
            needed.setdefault(name, []).append((frame_index, position))

    detections: list[dict[str, GroupDetection]] = [{} for _ in groups_frames]
    for name, entries in needed.items():
        model = models[name]
        capture = cv2.VideoCapture(str(directory / f"{name}.mkv"))
        try:
            for frame_index, position in sorted(entries):
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, image = capture.read()
                if not ok or image is None:
                    continue
                # cv2's stubs type read() loosely; decoded MJPG frames are uint8 BGR.
                detection = detector.detect(image.astype(np.uint8, copy=False))
                if (
                    not detection.found
                    or detection.ids is None
                    or detection.corners is None
                    or detection.count < min_corners
                ):
                    continue
                if single_marker:
                    ids = np.arange(detection.count, dtype=np.int32)  # corner index
                    pixels = detection.corners.reshape(-1, 2).astype(np.float64)
                else:
                    ids = detection.ids.reshape(-1).astype(np.int32)
                    order = np.argsort(ids)  # sorted: searchsorted in stereo_pairwise
                    ids = ids[order]
                    pixels = detection.corners.reshape(-1, 2).astype(np.float64)[order]
                normalized = cv2.undistortPoints(
                    pixels.reshape(-1, 1, 2), model.matrix, model.distortions
                ).reshape(-1, 2)
                detections[position][name] = GroupDetection(
                    ids=ids,
                    corners_px=pixels,
                    corners_norm=np.asarray(normalized, np.float64),
                )
        finally:
            capture.release()
    return [group for group in detections if len(group) >= 2]


def sweep_groups(
    directory: Path, names: list[str], window_s: float
) -> list[SyncGroup[int]]:
    """Synchronize a recorded sweep on its timestamp sidecars alone (no decoding).

    Offline grouping is **nearest-neighbour matching onto a reference timeline**
    (the camera with the most frames), NOT the live head-greedy pairing: real rigs
    record at per-camera EFFECTIVE rates that differ by 20%+ (frames skipped under
    load), and greedy head-windowing then fragments one physical instant into
    arbitrary small pairs — grouping a camera that sees the board with one that
    doesn't (real-rig bug). Here every reference frame becomes a candidate instant
    and each other camera contributes its closest unused frame within the window,
    so instants stay COMPLETE; a camera frame is consumed at most once.

    Payloads are per-camera frame indices; the same groups drive the Prepare
    scrubber (``GET /extrinsic/groups``) and the compute selection, so what the
    operator scrubs is exactly what the solver consumes.
    """
    stamps = {
        name: read_timestamps(directory / f"{name}.timestamps")
        for name in names
        if (directory / f"{name}.timestamps").is_file()
    }
    stamps = {name: series for name, series in stamps.items() if series}
    if not stamps:
        raise ValueError(f"no recorded timestamps under {directory}")

    reference = max(stamps, key=lambda name: len(stamps[name]))
    pointers = dict.fromkeys((n for n in stamps if n != reference), 0)

    groups: list[SyncGroup[int]] = []
    for ref_index, ref_time in enumerate(stamps[reference]):
        members = {reference: SyncFrame(reference, ref_time, ref_index)}
        for name, cursor in pointers.items():
            series = stamps[name]
            # Advance to this camera's frame closest to the reference instant.
            while cursor + 1 < len(series) and abs(series[cursor + 1] - ref_time) <= abs(
                series[cursor] - ref_time
            ):
                cursor += 1
            pointers[name] = cursor
            if cursor < len(series) and abs(series[cursor] - ref_time) <= window_s:
                members[name] = SyncFrame(name, series[cursor], cursor)
                pointers[name] = cursor + 1  # consumed: one group per frame
        if len(members) >= 2:
            timestamps = [frame.timestamp for frame in members.values()]
            groups.append(
                SyncGroup(
                    frames=members,
                    timestamp=sum(timestamps) / len(timestamps),
                    spread=max(timestamps) - min(timestamps),
                )
            )
    return groups


def compute_extrinsic_from_sweep(
    directory: Path,
    board: CalibrationBoard,
    models: list[CameraModel],
    *,
    anchor: str,
    window_s: float,
    stride: int | None = None,
    max_spread_s: float | None = None,
    min_shared: int = _DEFAULT_MIN_SHARED,
) -> tuple[ExtrinsicResult, BAInputs]:
    """Solve the camera array from a recorded synchronized sweep (ADR-0023).

    Synchronizes on the timestamp **sidecars only** (cheap), applies the Prepare
    knobs (``stride`` = every Nth group, ``max_spread_s`` = drop loosely-synced
    groups), evens the detection budget over the sweep, then detects only the
    selected frames and runs pairwise -> chaining -> triangulation -> BA. Also
    returns the BA observations so 'Minimize' can refine later without redetecting.
    Supports ChArUco boards and single-ArUco-marker targets (see board_object_points).
    """
    if len(models) < 2:
        raise ValueError("extrinsic calibration needs at least 2 cameras")
    by_name = {model.name: model for model in models}

    groups = sweep_groups(directory, [model.name for model in models], window_s)

    if max_spread_s is not None:
        groups = [group for group in groups if group.spread <= max_spread_s]
    if stride is not None and stride > 1:
        groups = groups[::stride]
    budget = (
        _MAX_DETECT_GROUPS
        if board.board_type is BoardType.CHARUCO
        else _MAX_DETECT_GROUPS_SINGLE_MARKER
    )
    if len(groups) > budget:
        step = len(groups) / budget
        groups = [groups[int(i * step)] for i in range(budget)]
    if not groups:
        raise ValueError("no synchronized groups in the sweep (check spread/stride)")
    logger.info("extrinsic compute: %d synchronized groups selected", len(groups))

    groups_frames = [
        {name: frame.payload for name, frame in group.frames.items()} for group in groups
    ]
    detections = _detect_group_frames(directory, groups_frames, by_name, board)
    if not detections:
        raise ValueError("no synchronized board views across >= 2 cameras")

    pairs = stereo_pairwise(detections, board, min_shared=min_shared)
    if not pairs:
        raise ValueError(f"no camera pair shares >= {min_shared} board views")

    names = [model.name for model in models]
    poses = chain_from_anchor(pairs, names, anchor)
    triangulation = triangulate_groups(detections, poses)
    order = triangulation.camera_order
    solved, points_opt = bundle_adjust(
        order,
        poses,
        triangulation.points3d,
        triangulation.obs_camera,
        triangulation.obs_point,
        triangulation.obs_norm,
        anchor,
    )
    per_camera, overall = pixel_errors(
        order,
        solved,
        points_opt,
        triangulation.obs_camera,
        triangulation.obs_point,
        triangulation.obs_px,
        by_name,
    )

    rotations: dict[str, list[float]] = {}
    translations: dict[str, list[float]] = {}
    for name, pose in solved.items():
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        rotations[name] = [float(v) for v in rvec.reshape(3)]
        translations[name] = [float(v) for v in pose[:3, 3]]

    chess = board_object_points(board)
    result = ExtrinsicResult(
        cameras=order,
        rotations=rotations,
        translations=translations,
        per_camera_error=per_camera,
        error=overall,
        pair_errors={f"{a}|{b}": pair.error for (a, b), pair in pairs.items()},
        group_count=len(detections),
        point_count=len(points_opt),
        points=[[float(v) for v in point] for point in points_opt],
        point_groups=[int(g) for g in triangulation.point_group],
        board_quads=_group_board_quads(
            triangulation.point_group,
            triangulation.point_corner,
            points_opt,
            chess,
            len(detections),
            min_corners=_min_corners(board),
        ),
    )
    ba_inputs = BAInputs(
        obs_camera=[int(v) for v in triangulation.obs_camera],
        obs_point=[int(v) for v in triangulation.obs_point],
        obs_norm=[[float(a), float(b)] for a, b in triangulation.obs_norm],
        obs_px=[[float(a), float(b)] for a, b in triangulation.obs_px],
        point_corner=[int(v) for v in triangulation.point_corner],
    )
    return result, ba_inputs
