"""Initial poses: pairwise stereo, chaining from the anchor, triangulation (ADR-0023)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic.model import (
    GroupDetection,
    PairEstimate,
    _common_ids,
    _min_corners,
    _natural_key,
    _transform,
    board_object_points,
)
from calibration_service.models.board import BoardType, CalibrationBoard

logger = logging.getLogger(__name__)


# Shared boards actually fed to stereoCalibrate per pair, picked for temporal
# diversity (Caliscope boards_sampled). The pairwise estimate only INITIALISES
# the chaining; the BA then consumes every kept group's observations.
_BOARDS_SAMPLED = 10


# Same, for single-ArUco targets: with only 4 corners per marker view, average
# over more views.
_BOARDS_SAMPLED_SINGLE_MARKER = 25


# stride (detection decimation over the candidate groups), max_groups (sharpest
# kept) and min_shared defaults live in calibration_service.tuning (ADR-0036);
# the transport layer resolves omitted request fields there per board type and
# always passes explicit values.
# stereoCalibrate refinement on normalized points (Caliscope criteria).
_STEREO_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)


def _best_shared_count(groups: list[dict[str, GroupDetection]], board: CalibrationBoard) -> int:
    """Shared board views of the BEST camera pair — same rule as stereo_pairwise.

    Diagnostic only: turns "no pair shares >= N views" into a message the
    operator can act on (how far off the sweep actually was).
    """
    min_corners = _min_corners(board)
    names = sorted({name for group in groups for name in group})
    best = 0
    for i, cam_a in enumerate(names):
        for cam_b in names[i + 1 :]:
            shared = 0
            for group in groups:
                det_a, det_b = group.get(cam_a), group.get(cam_b)
                if det_a is None or det_b is None:
                    continue
                if len(_common_ids(det_a, det_b)) >= min_corners:
                    shared += 1
            best = max(best, shared)
    return best


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
    min_shared: int,
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
                common = _common_ids(det_a, det_b)
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
                common = _common_ids(det_a, det_b)
                sel_a = np.searchsorted(det_a.ids, common)
                sel_b = np.searchsorted(det_b.ids, common)
                object_points.append(chess[common].astype(np.float32))
                points_a.append(det_a.corners_norm[sel_a].astype(np.float32))
                points_b.append(det_b.corners_norm[sel_b].astype(np.float32))

            identity = np.eye(3)
            zeros = np.zeros(5)
            try:
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
            except cv2.error as exc:
                # Degenerate shared views are a property of the sweep: report them
                # as unusable input (ValueError -> 422), naming the pair.
                raise ValueError(f"stereo calibration failed for {cam_a}-{cam_b}: {exc}") from exc
            rmse, _, _, _, _, rotation, translation = result[:7]
            pairs[(cam_a, cam_b)] = PairEstimate(
                rotation=np.asarray(rotation, np.float64),
                translation=np.asarray(translation, np.float64).reshape(3),
                error=float(rmse),
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
        current = min((c for c in cost if c not in visited), key=lambda c: cost[c], default=None)
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
    camera_order = sorted(poses, key=_natural_key)
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
