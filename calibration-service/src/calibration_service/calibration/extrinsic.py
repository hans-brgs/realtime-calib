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
from dataclasses import dataclass
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
from calibration_service.synchronization import FrameSynchronizer

logger = logging.getLogger(__name__)

# A pair needs this many common corners in a group for it to count as a shared
# board view (Caliscope legacy_stereocal min points; also the DLT/PnP floor).
_MIN_COMMON_CORNERS = 6
# Shared boards actually fed to stereoCalibrate per pair, picked for temporal
# diversity (Caliscope boards_sampled).
_BOARDS_SAMPLED = 10
# Minimum shared groups for a pair to be estimated at all (below this the
# geometry is too weak; the co-visibility gauge tells the operator live).
_DEFAULT_MIN_SHARED = 5
# Detection budget over the sweep: groups are subsampled evenly to this cap
# (detection dominates compute cost, like the intrinsic _MAX_DETECT_FRAMES).
_MAX_DETECT_GROUPS = 80
# stereoCalibrate refinement on normalized points (Caliscope criteria).
_STEREO_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
# Bundle-adjustment settings (Caliscope capture_volume least_squares kwargs).
_BA_FTOL = 1e-8
_BA_MAX_NFEV = 1000


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


def _transform(
    rotation: NDArray[np.float64], translation: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Build the 4x4 [R|t; 0 1] transform (x_b = R @ x_a + t)."""
    matrix = np.eye(4)
    matrix[:3, :3] = rotation
    matrix[:3, 3] = translation.reshape(3)
    return matrix


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

    For each pair, shared boards (>= ``_MIN_COMMON_CORNERS`` common ids in a
    group) are collected, subsampled for temporal diversity, and fed to
    ``cv2.stereoCalibrate`` in **normalized coordinates** (identity K, zero
    distortion, ``CALIB_FIX_INTRINSIC`` — only R|T are optimised, Caliscope).
    """
    chess = np.asarray(_cv_charuco_board(board).getChessboardCorners(), np.float64)
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
                if len(common) >= _MIN_COMMON_CORNERS:
                    shared.append((index, len(common)))
            if len(shared) < min_shared:
                continue

            object_points: list[NDArray[np.float32]] = []
            points_a: list[NDArray[np.float32]] = []
            points_b: list[NDArray[np.float32]] = []
            for index in _diverse_group_indices(shared, _BOARDS_SAMPLED):
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

    Builds a bidirectional transform graph from the pairwise estimates, fills
    missing pairs by bridging through intermediates (lowest cumulative error wins,
    Caliscope ``paired_pose_network``), then poses camera ``c`` as the anchor->c
    transform; the anchor itself is identity. Raises ``ValueError`` when a camera
    is unreachable from the anchor (no joint board views — guard-rail ADR-0012).
    """
    transforms: dict[tuple[str, str], tuple[NDArray[np.float64], float]] = {}
    for (cam_a, cam_b), pair in pairs.items():
        forward = _transform(pair.rotation, pair.translation)
        transforms[(cam_a, cam_b)] = (forward, pair.error)
        transforms[(cam_b, cam_a)] = (np.linalg.inv(forward), pair.error)

    changed = True
    while changed:
        changed = False
        for origin in sorted(cameras):
            for destination in sorted(cameras):
                if origin == destination or (origin, destination) in transforms:
                    continue
                best: tuple[NDArray[np.float64], float] | None = None
                for via in sorted(cameras):
                    left = transforms.get((origin, via))
                    right = transforms.get((via, destination))
                    if left is None or right is None:
                        continue
                    candidate = (right[0] @ left[0], left[1] + right[1])
                    if best is None or candidate[1] < best[1]:
                        best = candidate
                if best is not None:
                    transforms[(origin, destination)] = best
                    transforms[(destination, origin)] = (np.linalg.inv(best[0]), best[1])
                    changed = True

    poses: dict[str, NDArray[np.float64]] = {anchor: np.eye(4)}
    unreachable: list[str] = []
    for camera in cameras:
        if camera == anchor:
            continue
        entry = transforms.get((anchor, camera))
        if entry is None:
            unreachable.append(camera)
        else:
            poses[camera] = entry[0]
    if unreachable:
        raise ValueError(
            "cameras not co-visible with the anchor (need joint board views): "
            + ", ".join(sorted(unreachable))
        )
    return poses


def triangulate_groups(
    groups: list[dict[str, GroupDetection]],
    poses: dict[str, NDArray[np.float64]],
) -> tuple[
    NDArray[np.float64],
    NDArray[np.intp],
    NDArray[np.intp],
    NDArray[np.float64],
    NDArray[np.float64],
    list[str],
]:
    """Triangulate every corner seen by >= 2 cameras; build the BA observation set.

    DLT with **all** observing rays: per point, stack ``x*P2 - P0`` / ``y*P2 - P1``
    rows (P = the camera's normalized [R|t]) into a 2Nx4 system and take the SVD
    null-space — batched per camera-set like Caliscope ``point_data``. Returns
    ``(points3d, obs_camera, obs_point, obs_norm, obs_px, camera_order)`` where the
    ``obs_*`` arrays are flat parallel observation records for the BA.
    """
    camera_order = sorted(poses)
    camera_index = {name: i for i, name in enumerate(camera_order)}
    projections = {name: pose[:3, :] for name, pose in poses.items()}

    point_ids: dict[tuple[int, int], int] = {}
    point_obs: list[list[tuple[int, float, float, float, float]]] = []
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
                nx, ny = detection.corners_norm[row]
                px, py = detection.corners_px[row]
                point_obs[point].append((cam, float(nx), float(ny), float(px), float(py)))

    kept = [obs for obs in point_obs if len({cam for cam, *_ in obs}) >= 2]
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
    return (
        points3d,
        np.asarray(obs_camera, np.intp),
        np.asarray(obs_point, np.intp),
        np.asarray(obs_norm, np.float64),
        np.asarray(obs_px, np.float64),
        camera_order,
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
                rvec = np.zeros(3)
                tvec = np.zeros(3)
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

    result = least_squares(
        residuals,
        x0,
        jac_sparsity=sparsity,
        method="trf",
        x_scale="jac",
        loss="linear",
        ftol=_BA_FTOL,
        max_nfev=_BA_MAX_NFEV,
    )
    solution = np.asarray(result.x, np.float64)
    logger.info("bundle adjustment: cost %.6f -> %.6f", float(result.cost), float(result.cost))

    solved: dict[str, NDArray[np.float64]] = {anchor: np.eye(4)}
    for name, slot in free_slot.items():
        rmat, _ = cv2.Rodrigues(solution[6 * slot : 6 * slot + 3])
        solved[name] = _transform(
            np.asarray(rmat, np.float64), solution[6 * slot + 3 : 6 * slot + 6]
        )
    return solved, solution[n_cam_params:].reshape(-1, 3)


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
    per_camera: dict[str, float] = {}
    squared: list[NDArray[np.float64]] = []
    for cam, name in enumerate(camera_order):
        mask = obs_camera == cam
        if not bool(mask.any()):
            per_camera[name] = 0.0
            continue
        pose = poses[name]
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        model = models[name]
        projected, _ = cv2.projectPoints(
            points3d[obs_point[mask]], rvec, pose[:3, 3], model.matrix, model.distortions
        )
        diff = projected.reshape(-1, 2) - obs_px[mask]
        errors = np.asarray((diff**2).sum(axis=1), np.float64)
        per_camera[name] = float(np.sqrt(errors.mean()))
        squared.append(errors)
    overall = float(np.sqrt(np.concatenate(squared).mean())) if squared else 0.0
    return per_camera, overall


def _detect_group_frames(
    directory: Path,
    groups_frames: list[dict[str, int]],
    models: dict[str, CameraModel],
    board: CalibrationBoard,
) -> list[dict[str, GroupDetection]]:
    """Detect the board on each selected (camera, frame-index) and normalize corners."""
    detector = BoardDetector(board)
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
                    or detection.count < _MIN_COMMON_CORNERS
                ):
                    continue
                ids = detection.ids.reshape(-1).astype(np.int32)
                order = np.argsort(ids)  # sorted ids: searchsorted in stereo_pairwise
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
) -> ExtrinsicResult:
    """Solve the camera array from a recorded synchronized sweep (ADR-0023).

    Synchronizes on the timestamp **sidecars only** (cheap), applies the Prepare
    knobs (``stride`` = every Nth group, ``max_spread_s`` = drop loosely-synced
    groups), evens the detection budget over the sweep, then detects only the
    selected frames and runs pairwise -> chaining -> triangulation -> BA.
    """
    if board.board_type is not BoardType.CHARUCO:
        raise ValueError("extrinsic calibration requires a ChArUco board")
    if len(models) < 2:
        raise ValueError("extrinsic calibration needs at least 2 cameras")
    by_name = {model.name: model for model in models}

    stamps = {
        model.name: read_timestamps(directory / f"{model.name}.timestamps")
        for model in models
    }
    longest = max((len(s) for s in stamps.values()), default=0)
    if longest == 0:
        raise ValueError(f"no recorded timestamps under {directory}")
    synchronizer: FrameSynchronizer[int] = FrameSynchronizer(
        [model.name for model in models], window_s, max_buffer=longest + 1
    )
    for name, series in stamps.items():
        for frame_index, timestamp in enumerate(series):
            synchronizer.add(name, timestamp, frame_index)
    groups = synchronizer.drain()

    if max_spread_s is not None:
        groups = [group for group in groups if group.spread <= max_spread_s]
    if stride is not None and stride > 1:
        groups = groups[::stride]
    if len(groups) > _MAX_DETECT_GROUPS:
        step = len(groups) / _MAX_DETECT_GROUPS
        groups = [groups[int(i * step)] for i in range(_MAX_DETECT_GROUPS)]
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
    points3d, obs_camera, obs_point, obs_norm, obs_px, order = triangulate_groups(
        detections, poses
    )
    solved, points_opt = bundle_adjust(
        order, poses, points3d, obs_camera, obs_point, obs_norm, anchor
    )
    per_camera, overall = pixel_errors(
        order, solved, points_opt, obs_camera, obs_point, obs_px, by_name
    )

    rotations: dict[str, list[float]] = {}
    translations: dict[str, list[float]] = {}
    for name, pose in solved.items():
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        rotations[name] = [float(v) for v in rvec.reshape(3)]
        translations[name] = [float(v) for v in pose[:3, 3]]

    return ExtrinsicResult(
        cameras=order,
        rotations=rotations,
        translations=translations,
        per_camera_error=per_camera,
        error=overall,
        pair_errors={f"{a}|{b}": pair.error for (a, b), pair in pairs.items()},
        group_count=len(detections),
        point_count=len(points_opt),
    )
