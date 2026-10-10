"""The solve end to end: a compute from the sweep, and Minimize (ADR-0023)."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic.bundle import (
    BAStatus,
    _board_rigidity,
    _median_focal,
    _observation_residuals_px,
    bundle_adjust,
    pixel_errors,
    rigidity_mm,
)
from calibration_service.calibration.extrinsic.frame import _group_board_quads
from calibration_service.calibration.extrinsic.init import (
    _best_shared_count,
    chain_from_anchor,
    stereo_pairwise,
    triangulate_groups,
)
from calibration_service.calibration.extrinsic.model import (
    BAInputs,
    CameraModel,
    ExtrinsicResult,
    GroupDetection,
    _min_corners,
    _pose_lists,
    _result_poses,
    board_object_points,
)
from calibration_service.calibration.extrinsic.sweep import (
    _detect_group_frames,
    _motion_gate,
    _select_quality_groups,
    _warn_on_mixed_clocks,
    sweep_groups,
)
from calibration_service.models.board import CalibrationBoard

logger = logging.getLogger(__name__)


# Minimize = Caliscope's quality loop (filter_point_estimates -> optimize): drop
# the worst observations by CURRENT pixel residual, then re-fit. Product fraction
# is 2.5% in both Caliscope eras (legacy FILTERED_FRACTION, current
# filter_by_percentile_error(2.5)); min-per-camera floor from current Caliscope.
_REFINE_FILTER_FRACTION = 0.025


_REFINE_MIN_PER_CAMERA = 10


def _filter_observations(
    obs_camera: NDArray[np.intp],
    obs_point: NDArray[np.intp],
    residuals: NDArray[np.float64],
    fraction: float,
) -> NDArray[np.bool_]:
    """Keep-mask dropping the worst ``fraction`` of observations by residual.

    A GLOBAL percentile over euclidean pixel errors (Caliscope <= v0.5.4;
    v0.11.5 filters per camera), with two restore guards — every 3D point keeps
    >= 2 observations (stays constrained in the BA; legacy Caliscope deleted such
    points instead, but our scene arrays are index-aligned with groups, so points
    are kept), and every camera keeps >= _REFINE_MIN_PER_CAMERA observations
    (Caliscope v0.11.5 ``min_per_camera``). Restored slots are the lowest-residual
    trimmed ones. On single-marker sweeps most corners are seen by exactly two
    cameras, so the first guard restores most of the trimmed set (56 of 59 on
    session calib-07-13-2026, 2026-10-09 audit) — the ``observations_used`` count
    reports what was really dropped.
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
    poses = _result_poses(result)

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

    point_group = np.asarray(result.point_groups, np.intp)
    point_corner = np.asarray(ba_inputs.point_corner, np.int32)
    # The constraints a fresh solve applies today (ADR-0044, ADR-0046): the
    # physical board did not change because observations were filtered. A
    # ChArUco result solved before ADR-0046 gains its truss here, so its
    # geometry can jump on the first Minimize.
    focal_median = _median_focal(models)
    rigidity = _board_rigidity(point_group, point_corner, board, focal_median)
    solved, refined, ba_status = bundle_adjust(
        result.cameras,
        poses,
        points3d,
        obs_camera[keep],
        obs_point[keep],
        obs_norm[keep],
        anchor,
        rigidity,
        focal_median=focal_median,
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

    return _solved_result(
        cameras=result.cameras,
        board=board,
        solved=solved,
        points=refined,
        point_group=point_group,
        point_corner=point_corner,
        used_obs_camera=obs_camera[keep],
        per_camera=per_camera,
        overall=overall,
        ba_status=ba_status,
        group_count=result.group_count,
        pair_errors=result.pair_errors,
        # The Minimize report: how many observations survived the filter, out of
        # the FULL persisted set (this run always re-filters from it).
        observations_total=len(keep),
        # Minimize re-solves the same views: the refinement's counts still hold.
        border_refusals=result.border_refusals,
        border_attempts=result.border_attempts,
        moving_groups=result.moving_groups,
    )


def _solved_result(
    *,
    cameras: list[str],
    board: CalibrationBoard,
    solved: dict[str, NDArray[np.float64]],
    points: NDArray[np.float64],
    point_group: NDArray[np.intp],
    point_corner: NDArray[np.int32],
    used_obs_camera: NDArray[np.intp],
    per_camera: dict[str, float],
    overall: float,
    ba_status: BAStatus,
    group_count: int,
    pair_errors: dict[str, float],
    observations_total: int,
    border_refusals: dict[str, dict[str, int]] | None = None,
    border_attempts: dict[str, int] | None = None,
    moving_groups: int = 0,
) -> ExtrinsicResult:
    """The result of a bundle-adjusted solve: a fresh compute's or a Minimize's."""
    rotations, translations = _pose_lists(solved)
    return ExtrinsicResult(
        cameras=cameras,
        rotations=rotations,
        translations=translations,
        per_camera_error=per_camera,
        error=overall,
        pair_errors=pair_errors,
        group_count=group_count,
        point_count=len(points),
        points=[[float(v) for v in point] for point in points],
        point_groups=[int(g) for g in point_group],
        ba_converged=ba_status.converged,
        ba_nfev=ba_status.nfev,
        observations_used=len(used_obs_camera),
        observations_total=observations_total,
        per_camera_observations={
            name: int((used_obs_camera == index).sum()) for index, name in enumerate(cameras)
        },
        rigidity_mm=rigidity_mm(points, point_group, point_corner, board),
        border_refusals=border_refusals or {},
        border_attempts=border_attempts or {},
        moving_groups=moving_groups,
        board_quads=_group_board_quads(
            point_group,
            point_corner,
            points,
            board_object_points(board),
            group_count,
            min_corners=_min_corners(board),
        ),
    )


def compute_extrinsic_from_sweep(
    directory: Path,
    board: CalibrationBoard,
    models: list[CameraModel],
    *,
    anchor: str,
    window_s: float,
    stride: int,
    max_groups: int,
    min_shared: int,
    max_spread_s: float | None = None,
    max_motion_px: float | None = None,
) -> tuple[ExtrinsicResult, BAInputs]:
    """Solve the camera array from a recorded synchronized sweep (ADR-0023/0033).

    Synchronizes on the timestamp **sidecars only** (cheap), drops loosely-synced
    groups (``max_spread_s``), detects on 1 candidate group every ``stride``
    (the cost knob, mirroring the intrinsic Prepare — ADR-0036), then keeps the
    ~``max_groups`` SHARPEST groups (observe first, choose after, ADR-0033 —
    blind uniform sampling injected motion-blurred corners into the BA) and runs
    pairwise -> chaining -> triangulation -> BA. Also returns the BA observations
    so 'Minimize' can refine later without redetecting. Supports ChArUco boards
    and single-ArUco-marker targets (see board_object_points).

    ``max_motion_px`` gates the detected groups on the board's motion between their
    members' captures (ADR-0056; None = no gate). Should the gated pool not solve,
    the array is solved again from every detected group, with a warning.
    """
    if len(models) < 2:
        raise ValueError("extrinsic calibration needs at least 2 cameras")
    by_name = {model.name: model for model in models}
    _warn_on_mixed_clocks(directory)

    groups = sweep_groups(directory, [model.name for model in models], window_s)

    if max_spread_s is not None:
        groups = [group for group in groups if group.spread <= max_spread_s]
    groups = groups[:: max(1, stride)]
    if not groups:
        raise ValueError("no synchronized groups in the sweep (check the spread threshold)")
    logger.info("extrinsic compute: detecting on %d candidate groups", len(groups))

    groups_frames = [
        {name: frame.payload for name, frame in group.frames.items()} for group in groups
    ]
    detected = _detect_group_frames(directory, groups_frames, by_name, board)
    if not detected.groups:
        raise ValueError("no synchronized board views across >= 2 cameras")
    pool, moving = _motion_gate(detected, max_motion_px, max_groups)
    try:
        result, ba_inputs = _solve_groups(
            pool, board, models, anchor=anchor, max_groups=max_groups, min_shared=min_shared
        )
    except ValueError:
        if not moving:
            raise
        logger.warning(
            "the motion gate left no solvable array (%d of %d groups dropped): "
            "solving from every detected group",
            moving,
            len(detected.groups),
        )
        moving = 0
        result, ba_inputs = _solve_groups(
            detected.groups,
            board,
            models,
            anchor=anchor,
            max_groups=max_groups,
            min_shared=min_shared,
        )
    result = replace(
        result,
        border_refusals=detected.refusals,
        border_attempts=detected.attempts,
        moving_groups=moving,
    )
    return result, ba_inputs


def _solve_groups(
    detections: list[dict[str, GroupDetection]],
    board: CalibrationBoard,
    models: list[CameraModel],
    *,
    anchor: str,
    max_groups: int,
    min_shared: int,
) -> tuple[ExtrinsicResult, BAInputs]:
    """Keep the sharpest groups and solve the array from them (ADR-0023/0033)."""
    by_name = {model.name: model for model in models}
    selected = _select_quality_groups(detections, max(1, max_groups))
    logger.info(
        "extrinsic compute: kept %d/%d detected groups by sharpness (cap %d)",
        len(selected),
        len(detections),
        max_groups,
    )
    detections = selected

    pairs = stereo_pairwise(detections, board, min_shared=min_shared)
    if not pairs:
        # Actionable (ADR-0036): name the best pair the sweep actually managed, so
        # the operator knows whether to re-sweep or lower the API-only min_shared.
        best = _best_shared_count(detections, board)
        raise ValueError(
            f"no camera pair shares >= {min_shared} board views "
            f"(best pair: {best}) — sweep the board where the cameras overlap, "
            f"or lower 'min_shared' (API-only knob)"
        )

    names = [model.name for model in models]
    poses = chain_from_anchor(pairs, names, anchor)
    triangulation = triangulate_groups(detections, poses)
    order = triangulation.camera_order
    focal_median = _median_focal(models)
    rigidity = _board_rigidity(
        triangulation.point_group, triangulation.point_corner, board, focal_median
    )
    solved, points_opt, ba_status = bundle_adjust(
        order,
        poses,
        triangulation.points3d,
        triangulation.obs_camera,
        triangulation.obs_point,
        triangulation.obs_norm,
        anchor,
        rigidity,
        focal_median=focal_median,
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

    result = _solved_result(
        cameras=order,
        board=board,
        solved=solved,
        points=points_opt,
        point_group=triangulation.point_group,
        point_corner=triangulation.point_corner,
        # A full solve uses every observation (Minimize is what filters).
        used_obs_camera=triangulation.obs_camera,
        per_camera=per_camera,
        overall=overall,
        ba_status=ba_status,
        group_count=len(detections),
        pair_errors={f"{a}|{b}": pair.error for (a, b), pair in pairs.items()},
        observations_total=len(triangulation.obs_camera),
    )
    ba_inputs = BAInputs(
        obs_camera=[int(v) for v in triangulation.obs_camera],
        obs_point=[int(v) for v in triangulation.obs_point],
        obs_norm=[[float(a), float(b)] for a, b in triangulation.obs_norm],
        obs_px=[[float(a), float(b)] for a, b in triangulation.obs_px],
        point_corner=[int(v) for v in triangulation.point_corner],
    )
    return result, ba_inputs
