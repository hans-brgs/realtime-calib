"""The world frame: framing, rotations, reorientation, centres (ADR-0026, ADR-0057)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic.model import (
    ExtrinsicResult,
    _pose_lists,
    _result_poses,
    _transform,
)


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
    min_corners: int,
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
    quad: list[list[float]],
    *,
    at_center: bool = False,
    ground: bool = False,
    normal_behind: bool = False,
) -> NDArray[np.float64]:
    """World-frame change G placing the origin + axes on a board quad ('Set origin').

    The quad's corner order (c0 bl, c1 br, c3 tl) defines the board basis B
    (board->world); the new world IS that board frame: ``x_new = B^-1 x_old``.
    ``at_center`` anchors the origin on the quad centroid instead of c0 — the
    single-ArUco convention (cv2 places the marker frame at its CENTER), whereas
    a ChArUco board frame originates at its first chessboard corner.
    ``ground`` ('Set ground'): the operator declares the board LYING ON THE
    FLOOR, so its normal becomes the world's up. The new world is the
    OpenCV-oriented ground frame — x along the board's x edge, y = -normal
    (down), z along the board's y edge (proper rotation) — which every export
    basis (they all map canonical -y to the platform's up) turns into a flat
    floor with no manual reorientation. ``normal_behind`` says the quad's normal
    (x cross y of its corner order) points behind the printed face: a ChArUco's
    chessboard corners run y down, so its normal points away from the cameras and
    put them under the floor (ADR-0057); the board frame then turns half a turn
    about its x axis. A single marker's corners run y up: its normal faces the
    cameras. The corner order decides, not the cameras' positions: their centroid
    sits behind a steeply tilted target that every camera sees from the front.
    The basis is re-orthonormalised (the Kabsch quad is rigid, but guard anyway).
    """
    corners = np.asarray(quad, np.float64)
    x = corners[1] - corners[0]
    x = x / np.linalg.norm(x)
    y_raw = corners[3] - corners[0]
    y = y_raw - x * float(x @ y_raw)
    y = y / np.linalg.norm(y)
    z = np.cross(x, y)
    anchor = corners.mean(axis=0) if at_center else corners[0]
    if ground and normal_behind:
        y, z = -y, -z
    basis = np.column_stack([x, -z, y] if ground else [x, y, z])  # board->world
    g_rotation = basis.T
    g_translation = -basis.T @ anchor
    return _transform(g_rotation, g_translation)


def camera_centres(result: ExtrinsicResult) -> dict[str, NDArray[np.float64]]:
    """Each camera's optical centre in the result's world (``-R^T t``, target units)."""
    return {n: -pose[:3, :3].T @ pose[:3, 3] for n, pose in _result_poses(result).items()}


def reorient_result(result: ExtrinsicResult, transform: NDArray[np.float64]) -> ExtrinsicResult:
    """Re-express the solved array in a new world frame (rigid G: old->new coords).

    Cameras: ``x_cam = R x_old + t`` with ``x_old = G^-1 x_new`` gives
    ``R' = R G_R^T``, ``t' = t - R' G_t``. Points/quads map as ``G_R p + G_t``.
    Reprojection errors are invariant under a rigid world change, so all quality
    fields carry over unchanged.
    """
    g_rotation = transform[:3, :3]
    g_translation = transform[:3, 3]

    reposed: dict[str, NDArray[np.float64]] = {}
    for name, pose in _result_poses(result).items():
        new_r = pose[:3, :3] @ g_rotation.T
        reposed[name] = _transform(new_r, pose[:3, 3] - new_r @ g_translation)
    rotations, translations = _pose_lists(reposed)

    points = np.asarray(result.points, np.float64)
    moved_points = points @ g_rotation.T + g_translation if len(points) else points
    quads: list[list[list[float]] | None] = []
    for quad in result.board_quads:
        if quad is None:
            quads.append(None)
        else:
            moved = np.asarray(quad, np.float64) @ g_rotation.T + g_translation
            quads.append([[float(v) for v in corner] for corner in moved])

    # dataclasses.replace: quality fields and diagnostics (ba_converged, nfev,
    # observation counts, framed_group) carry over untouched — a rigid world
    # change alters geometry expression only.
    return replace(
        result,
        rotations=rotations,
        translations=translations,
        points=[[float(v) for v in point] for point in moved_points],
        board_quads=quads,
    )
