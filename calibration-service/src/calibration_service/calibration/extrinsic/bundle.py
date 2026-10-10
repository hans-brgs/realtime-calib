"""The bundle adjustment, the target's rigidity, the pixel errors (ADR-0044, ADR-0046)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations

import cv2
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares  # type: ignore[import-untyped]
from scipy.sparse import lil_matrix  # type: ignore[import-untyped]

from calibration_service.calibration.extrinsic.model import (
    CameraModel,
    _pose,
    _rvec_t,
    board_object_points,
    board_unit_mm,
)
from calibration_service.models.board import CalibrationBoard
from calibration_service.tuning import TUNING

logger = logging.getLogger(__name__)


# Bundle-adjustment settings: a linear pass, then a robust pass from its solution
# (Caliscope v0.11.5 runs the same two stages). The robust loss is soft_l1 at a
# 1 px residual scale (ADR-0046): one bad 4-corner marker view poisons a linear BA
# (observed 17.7 px on the real rig), and the former Huber pass crawled to the
# _BA_MAX_NFEV ceiling on real sweeps (1005 evaluations, 15 s, flagged truncated)
# where soft_l1 converges in tens. The scale is stated in PIXELS and converted
# with the array's median focal, so the outlier threshold no longer drifts with
# the lens (a fixed normalized 0.0015 meant 0.9 px at f=600, 2 px at f=1350).
# ftol stays at 1e-8 on both passes where Caliscope loosens the robust one to
# 1e-4: soft_l1 converges at 1e-8 anyway, and 1e-4 moves no camera by more than
# 0.2 mm on the recorded sessions.
_BA_FTOL = 1e-8


_BA_MAX_NFEV = 1000


_BA_ROBUST_LOSS = "soft_l1"


_BA_ROBUST_SCALE_PX = 1.0


@dataclass(frozen=True)
class RigidityConstraints:
    """Known distances between reconstructed corner pairs (ADR-0044, ADR-0046).

    The BA otherwise treats a group's corners as independent 3D points and is
    free to deform the target to absorb detection/sync noise. Each row pins one
    pair to the distance the physical board mandates:
    ``residual = (||P_i - P_j|| - distance) * weight``.

    Units: ``distance`` in board units (the solver's world scale); ``weight``
    already whitened for the normalized residual space so these rows are
    commensurable with the reprojection ones.
    """

    point_a: NDArray[np.intp]  # (C,) index into points3d
    point_b: NDArray[np.intp]  # (C,)
    distance: NDArray[np.float64]  # (C,) expected separation, board units
    weight: NDArray[np.float64]  # (C,)

    def __len__(self) -> int:
        return len(self.point_a)


def _board_truss(grid: NDArray[np.int64]) -> list[tuple[int, int]]:
    """Corner-id pairs a rigid board pins: its local truss (Caliscope v0.11.5).

    ``grid`` holds the integer grid position of every board corner, indexed by
    corner id. Horizontal and vertical neighbours plus both diagonals of every
    cell, built on the FULL board as Caliscope's ``_truss_distance_constraints``
    does: a view then keeps each edge whose two corners it triangulated, so a
    hole only costs the edges touching it. Linear in the corner count, where all
    pairs grow quadratically. A single marker is one cell: its 4 sides + 2
    diagonals, i.e. all 6 pairs.
    """
    at = {(int(x), int(y)): i for i, (x, y) in enumerate(grid)}
    pairs: set[tuple[int, int]] = set()

    def link(a: int | None, b: int | None) -> None:
        if a is not None and b is not None:
            pairs.add((min(a, b), max(a, b)))

    for (x, y), i in at.items():
        right, up = at.get((x + 1, y)), at.get((x, y + 1))
        link(i, right)
        link(i, up)
        link(i, at.get((x + 1, y + 1)))
        link(right, up)
    return sorted(pairs)


def _extreme_braces(grid: NDArray[np.int64]) -> list[tuple[int, int]]:
    """Pairs (indices into ``grid``) joining one view's four extreme corners.

    Neighbour and cell-diagonal distances all survive a fold along a grid line;
    braces between the extremes (min/max of x+y and x-y) cross every fold.
    Caliscope braces the board's four FIXED corners, which a partial view lacks;
    on a full view both rules pick the same corners. Synthetic ground truth,
    median of the worst camera-centre error over 12 draws: 6.07 -> 5.98 mm on
    partial views (p90 8.27 -> 7.26), 8.52 -> 7.89 mm with 15 % of the corners
    of each view hidden as well; on full views with hidden corners the two rules
    are even (4.72 vs 4.94 mm median, 6.61 vs 6.36 mm p90).
    """
    along, across = grid[:, 0] + grid[:, 1], grid[:, 0] - grid[:, 1]
    extremes = {
        int(np.argmin(along)),
        int(np.argmax(along)),
        int(np.argmin(across)),
        int(np.argmax(across)),
    }
    return list(combinations(sorted(extremes), 2))


def build_rigidity_constraints(
    point_group: NDArray[np.intp],
    point_corner: NDArray[np.int32],
    board: CalibrationBoard,
    focal_median: float,
    *,
    sigma_mm: float,
) -> RigidityConstraints | None:
    """Per group, the board corner pairs tied to their physical distances.

    A group is one synchronized view of the board. Its pairs are the board truss
    edges whose two corners the group triangulated (``_board_truss``), plus
    braces across its extreme corners (``_extreme_braces``); a single marker
    gets its 6 pairs from the truss of its one cell. Weights follow
    Caliscope: ``(1 px / f_median) / sigma_units`` — one pixel of reprojection is
    the yardstick, so a deviation of ``sigma`` costs about one pixel.
    ``sigma_mm`` is converted to board units via the board's physical unit,
    keeping the whole residual vector dimensionless.

    Returns ``None`` when the target's geometry cannot pin anything (fewer than
    two corners triangulated in every group).
    """
    reference = board_object_points(board)
    unit_mm = board_unit_mm(board)
    if unit_mm <= 0.0 or focal_median <= 0.0:
        return None
    # Fail loud (ADR-0036): corner ids indexing past the board's geometry mean
    # the points came from a DIFFERENT board than the one constraining them —
    # silently tying corners to wrong distances would mis-calibrate while
    # reporting success.
    if len(point_corner) and int(point_corner.max()) >= len(reference):
        raise ValueError(
            f"corner id {int(point_corner.max())} exceeds the board geometry "
            f"({len(reference)} points): observations and board disagree"
        )
    sigma_units = sigma_mm / unit_mm
    weight = (1.0 / focal_median) / sigma_units
    # Integer grid positions: ChArUco corners sit one board unit apart, and so do
    # a single marker's (its unit is the marker side).
    grid = np.rint(reference[:, :2] - reference[:, :2].min(axis=0)).astype(np.int64)
    truss = _board_truss(grid)

    a_list: list[int] = []
    b_list: list[int] = []
    distances: list[float] = []
    for group in np.unique(point_group):
        members = np.flatnonzero(point_group == group)
        if len(members) < 2:
            continue
        corners = point_corner[members]
        local = {int(corner): slot for slot, corner in enumerate(corners)}
        pairs = {
            (min(local[a], local[b]), max(local[a], local[b]))
            for a, b in truss
            if a in local and b in local
        }
        pairs.update(_extreme_braces(grid[corners]))
        for i, j in sorted(pairs):
            a_list.append(int(members[i]))
            b_list.append(int(members[j]))
            distances.append(float(np.linalg.norm(reference[corners[i]] - reference[corners[j]])))
    if not a_list:
        return None
    return RigidityConstraints(
        point_a=np.asarray(a_list, np.intp),
        point_b=np.asarray(b_list, np.intp),
        distance=np.asarray(distances, np.float64),
        weight=np.full(len(a_list), weight, np.float64),
    )


def rigidity_mm(
    points3d: NDArray[np.float64],
    point_group: NDArray[np.intp],
    point_corner: NDArray[np.int32],
    board: CalibrationBoard,
) -> float:
    """RMS deviation (mm) of the reconstructed corner pairs from the real board.

    The reprojection-independent quality judge (ADR-0044): a BA can always lower
    its residuals by deforming the target, and this number does not move when it
    does. Scale-sensitive too — a world 2% too large shows up here. Judged over
    EVERY within-group pair, not only the truss the solver constrains. Returns
    0.0 when no group has two triangulated corners.
    """
    reference = board_object_points(board) * board_unit_mm(board)
    scaled = points3d * board_unit_mm(board)
    deviations: list[NDArray[np.float64]] = []
    for group in np.unique(point_group):
        members = np.flatnonzero(point_group == group)
        if len(members) < 2:
            continue
        first, second = np.triu_indices(len(members), k=1)
        corners = point_corner[members]
        expected = np.linalg.norm(reference[corners[first]] - reference[corners[second]], axis=1)
        measured = np.linalg.norm(scaled[members[first]] - scaled[members[second]], axis=1)
        deviations.append(measured - expected)
    if not deviations:
        return 0.0
    return float(np.sqrt(np.mean(np.concatenate(deviations) ** 2)))


@dataclass(frozen=True)
class BAStatus:
    """Bundle-adjustment outcome (ADR-0036 observability).

    ``converged`` is False when either pass stopped on the ``_BA_MAX_NFEV``
    ceiling rather than a tolerance — the solution is then the best-so-far, not
    an optimum, and the operator deserves to know.
    """

    converged: bool
    nfev: int  # function evaluations across both passes


def bundle_adjust(
    camera_order: list[str],
    poses: dict[str, NDArray[np.float64]],
    points3d: NDArray[np.float64],
    obs_camera: NDArray[np.intp],
    obs_point: NDArray[np.intp],
    obs_norm: NDArray[np.float64],
    anchor: str,
    rigidity: RigidityConstraints | None = None,
    *,
    focal_median: float,
) -> tuple[dict[str, NDArray[np.float64]], NDArray[np.float64], BAStatus]:
    """Jointly refine non-anchor poses + 3D points on normalized reprojection error.

    Parameter vector = ``[rvec|tvec per NON-anchor camera, then xyz per point]``;
    the anchor stays identity (gauge fixed, ADR-0023). Sparse Jacobian: each
    residual row touches its camera's 6 params (unless anchor) + its point's 3.
    Residuals in normalized coords (undistorted upstream), projected with K=I —
    Caliscope's ``use_normalized`` mode (Triggs et al. conditioning). Also returns
    a :class:`BAStatus` so a truncated solve never passes for a converged one.

    ``rigidity`` (ADR-0044) appends one whitened distance residual per known
    corner pair, so the target cannot deform to absorb noise. Those rows also
    make the world SCALE observable, removing the last gauge mode of the
    unconstrained problem (see the module docstring). ``None`` keeps the pure
    reprojection behaviour. ``focal_median`` (px, native) converts the robust
    loss scale from pixels to the normalized residual space (ADR-0046).
    """
    if not (np.isfinite(focal_median) and focal_median > 0.0):
        raise ValueError(f"focal_median must be a positive focal (px), got {focal_median!r}")
    free = [name for name in camera_order if name != anchor]
    free_slot = {name: i for i, name in enumerate(free)}
    n_cam_params = 6 * len(free)
    n_obs = len(obs_camera)

    x0 = np.zeros(n_cam_params + 3 * len(points3d))
    for name, slot in free_slot.items():
        x0[6 * slot : 6 * slot + 3], x0[6 * slot + 3 : 6 * slot + 6] = _rvec_t(poses[name])
    x0[n_cam_params:] = points3d.ravel()

    # The anchor is held at its CURRENT pose (identity right after chaining, but a
    # reorientation may have moved the world frame — Minimize must not undo it).
    anchor_rvec, anchor_tvec = _rvec_t(poses[anchor])

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
        reprojection = (projected - obs_norm).ravel()
        if rigidity is None:
            return np.asarray(reprojection, np.float64)
        spans = points[rigidity.point_a] - points[rigidity.point_b]
        measured = np.linalg.norm(spans, axis=1)
        deviation = (measured - rigidity.distance) * rigidity.weight
        return np.asarray(np.concatenate([reprojection, deviation]), np.float64)

    n_rigid = 0 if rigidity is None else len(rigidity)
    sparsity = lil_matrix((2 * n_obs + n_rigid, len(x0)), dtype=int)
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
    if rigidity is not None:
        # A distance residual sees only its two points' xyz — no camera params:
        # rigidity constrains the point cloud's SHAPE, and the poses follow
        # through the reprojection rows.
        rigid_rows = 2 * n_obs + np.arange(n_rigid)
        for point_index in (rigidity.point_a, rigidity.point_b):
            for k in range(3):
                sparsity[rigid_rows, n_cam_params + 3 * point_index + k] = 1

    # Two-stage solve: a linear pass first (full gradients converge the geometry
    # from the chained init), then the robust pass from that solution so residual
    # outliers — e.g. a misdetected 4-corner marker view — stop steering the fit.
    # A robust loss alone stalls from a coarse init (everything starts beyond
    # f_scale). Rigidity rows go through the same robust pass.
    common = {
        "jac_sparsity": sparsity,
        "method": "trf",
        "x_scale": "jac",
        "ftol": _BA_FTOL,
        "max_nfev": _BA_MAX_NFEV,
    }
    first = least_squares(residuals, x0, loss="linear", **common)
    result = least_squares(
        residuals,
        np.asarray(first.x, np.float64),
        loss=_BA_ROBUST_LOSS,
        f_scale=_BA_ROBUST_SCALE_PX / focal_median,
        **common,
    )
    solution = np.asarray(result.x, np.float64)
    # scipy status: 0 = max_nfev ceiling hit (truncated), > 0 = a tolerance was
    # satisfied. Both passes must converge for the answer to be an optimum.
    status = BAStatus(
        converged=int(first.status) > 0 and int(result.status) > 0,
        nfev=int(first.nfev) + int(result.nfev),
    )
    logger.info(
        "bundle adjustment: cost %.6f -> %.6f (%s, nfev=%d)",
        float(first.cost),
        float(result.cost),
        "converged" if status.converged else "TRUNCATED at the max_nfev ceiling",
        status.nfev,
    )
    if not status.converged:
        logger.warning("bundle adjustment hit max_nfev=%d: poses are best-so-far", _BA_MAX_NFEV)

    solved: dict[str, NDArray[np.float64]] = {anchor: poses[anchor].copy()}
    for name, slot in free_slot.items():
        solved[name] = _pose(
            solution[6 * slot : 6 * slot + 3], solution[6 * slot + 3 : 6 * slot + 6]
        )
    return solved, solution[n_cam_params:].reshape(-1, 3), status


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
        rvec, tvec = _rvec_t(poses[name])
        model = models[name]
        projected, _ = cv2.projectPoints(
            points3d[obs_point[mask]], rvec, tvec, model.matrix, model.distortions
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


def _median_focal(models: list[CameraModel]) -> float:
    """The array's median focal (px, native): the BA's pixel yardstick.

    The median, not the mean, so one camera's outlier intrinsics cannot set the
    scale of the robust loss or of the rigidity weights (Caliscope).
    """
    return float(np.median([model.matrix[0, 0] for model in models]))


def _board_rigidity(
    point_group: NDArray[np.intp],
    point_corner: NDArray[np.int32],
    board: CalibrationBoard,
    focal_median: float,
) -> RigidityConstraints | None:
    """Rigidity rows for a solve, for both board types (ADR-0044, ADR-0046).

    ChArUco used to opt out ("its 40+ corners constrain the fit through sheer
    count"): measured, the unconstrained BA deformed the board (rigidity 3.7 ->
    7.0 mm) and let the scale drift (+1.15 %); with the truss, camera-centre
    error vs synthetic ground truth drops 34 %.
    """
    return build_rigidity_constraints(
        point_group,
        point_corner,
        board,
        focal_median,
        sigma_mm=TUNING.rigidity_sigma_mm,
    )
