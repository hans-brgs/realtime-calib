"""Extrinsic solver numerical tests on a synthetic 3-camera rig (ADR-0023).

Ground-truth world->cam poses; a ChArUco board moved through the shared volume;
exact normalized/pixel projections. Each stage must recover the truth: pairwise
stereo, transitive chaining (with a bridged pair), DLT triangulation, and the
anchor-fixed bundle adjustment. The full sweep orchestration is covered with
synthetic detections injected in place of video I/O (no end-to-end video test
here — that is manual verification in Docker).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic import (
    CameraModel,
    GroupDetection,
    PairEstimate,
    _transform,
    bundle_adjust,
    chain_from_anchor,
    compute_extrinsic_from_sweep,
    pixel_errors,
    stereo_pairwise,
    triangulate_groups,
)
from calibration_service.calibration.intrinsic import _cv_charuco_board
from calibration_service.models.board import BoardType, CalibrationBoard

BOARD = CalibrationBoard(
    board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
)
CHESS = np.asarray(_cv_charuco_board(BOARD).getChessboardCorners(), np.float64)
K = np.array([[800.0, 0.0, 320.0], [0.0, 800.0, 240.0], [0.0, 0.0, 1.0]])
DIST = np.zeros(5)


def _rot(axis: str, degrees: float) -> NDArray[np.float64]:
    radians = np.radians(degrees)
    c, s = np.cos(radians), np.sin(radians)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], np.float64)
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], np.float64)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)


# Ground truth world->cam poses; cam_0 is the anchor (identity).
POSES = {
    "cam_0": _transform(np.eye(3), np.zeros(3)),
    "cam_1": _transform(_rot("y", 15.0), np.array([-2.0, 0.1, 1.0])),
    "cam_2": _transform(_rot("y", -12.0) @ _rot("x", 4.0), np.array([2.0, 0.4, 0.8])),
}


def _board_world(group: int) -> NDArray[np.float64]:
    """Board corners in world coords for one group (board moved + tilted per group)."""
    tilt = _rot("x", 8.0 * np.sin(group * 1.3)) @ _rot("y", 10.0 * np.cos(group * 0.9))
    offset = np.array([-3.0 + 0.9 * group, -2.6 + 0.3 * group, 9.0 + 0.5 * group])
    return CHESS @ tilt.T + offset


def _project(world: NDArray[np.float64], pose: NDArray[np.float64]) -> tuple[
    NDArray[np.float64], NDArray[np.float64]
]:
    """Exact normalized + pixel projections of world points into a camera."""
    cam = world @ pose[:3, :3].T + pose[:3, 3]
    norm = cam[:, :2] / cam[:, 2:3]
    px = norm * K[0, 0] + np.array([K[0, 2], K[1, 2]])
    return norm, px


def _groups(count: int = 6) -> list[dict[str, GroupDetection]]:
    ids = np.arange(len(CHESS), dtype=np.int32)
    groups: list[dict[str, GroupDetection]] = []
    for g in range(count):
        world = _board_world(g)
        group: dict[str, GroupDetection] = {}
        for name, pose in POSES.items():
            norm, px = _project(world, pose)
            group[name] = GroupDetection(ids=ids, corners_px=px, corners_norm=norm)
        groups.append(group)
    return groups


def test_stereo_pairwise_recovers_relative_poses() -> None:
    pairs = stereo_pairwise(_groups(), BOARD, min_shared=3)
    assert set(pairs) == {("cam_0", "cam_1"), ("cam_0", "cam_2"), ("cam_1", "cam_2")}
    # cam_0 is identity, so the (cam_0 -> cam_1) transform IS cam_1's pose.
    pair = pairs[("cam_0", "cam_1")]
    assert np.allclose(pair.rotation, POSES["cam_1"][:3, :3], atol=1e-3)
    assert np.allclose(pair.translation, POSES["cam_1"][:3, 3], atol=1e-3)
    assert pair.error < 1e-3  # exact synthetic data


def test_chain_bridges_a_missing_pair() -> None:
    # Only (0,1) and (1,2) provided: 0->2 must be bridged as T_12 @ T_01.
    t01 = POSES["cam_1"]
    t12 = POSES["cam_2"] @ np.linalg.inv(POSES["cam_1"])
    pairs = {
        ("cam_0", "cam_1"): PairEstimate(t01[:3, :3], t01[:3, 3], 0.1, 5),
        ("cam_1", "cam_2"): PairEstimate(t12[:3, :3], t12[:3, 3], 0.2, 5),
    }
    poses = chain_from_anchor(pairs, ["cam_0", "cam_1", "cam_2"], "cam_0")
    assert np.allclose(poses["cam_0"], np.eye(4))
    assert np.allclose(poses["cam_2"], POSES["cam_2"], atol=1e-9)


def test_chain_raises_on_unreachable_camera() -> None:
    t01 = POSES["cam_1"]
    pairs = {("cam_0", "cam_1"): PairEstimate(t01[:3, :3], t01[:3, 3], 0.1, 5)}
    with pytest.raises(ValueError, match="cam_2"):
        chain_from_anchor(pairs, ["cam_0", "cam_1", "cam_2"], "cam_0")


def test_triangulation_recovers_world_points() -> None:
    groups = _groups(3)
    tri = triangulate_groups(groups, POSES)
    assert tri.camera_order == ["cam_0", "cam_1", "cam_2"]
    assert len(tri.points3d) == 3 * len(CHESS)  # every corner seen by all cameras
    assert len(tri.obs_camera) == len(tri.obs_point) == len(tri.obs_norm) == len(tri.obs_px)
    assert set(tri.point_group.tolist()) == {0, 1, 2}
    # First group's points must match its ground-truth world coords (order of
    # first-encounter: group 0 corners come first).
    assert np.allclose(tri.points3d[: len(CHESS)], _board_world(0), atol=1e-6)


def test_bundle_adjust_recovers_truth_with_anchor_fixed() -> None:
    groups = _groups(4)
    tri = triangulate_groups(groups, POSES)
    points3d, order = tri.points3d, tri.camera_order

    # Perturb the non-anchor poses + the points; observations stay exact.
    rng = np.random.default_rng(7)
    perturbed = {name: pose.copy() for name, pose in POSES.items()}
    for name in ("cam_1", "cam_2"):
        wiggle, _ = cv2.Rodrigues(rng.normal(0.0, 0.015, 3))
        perturbed[name][:3, :3] = np.asarray(wiggle) @ perturbed[name][:3, :3]
        perturbed[name][:3, 3] += rng.normal(0.0, 0.05, 3)
    noisy_points = points3d + rng.normal(0.0, 0.03, points3d.shape)

    solved, refined = bundle_adjust(
        order, perturbed, noisy_points, tri.obs_camera, tri.obs_point, tri.obs_norm, "cam_0"
    )
    assert np.allclose(solved["cam_0"], np.eye(4))  # anchor fixed by construction
    for name in ("cam_1", "cam_2"):
        assert np.allclose(solved[name][:3, :3], POSES[name][:3, :3], atol=1e-4)
        assert np.allclose(solved[name][:3, 3], POSES[name][:3, 3], atol=1e-3)
    # Global scale is an unobservable gauge mode of the BA (see module docstring):
    # scaling points + translations leaves every normalized projection unchanged,
    # so the noisy init's slight scale bias survives. Compare up to that scale.
    scale = float((refined * points3d).sum() / (points3d**2).sum())
    assert abs(scale - 1.0) < 2e-3  # inherited from the init, not amplified
    assert np.allclose(refined, scale * points3d, atol=5e-4)


def test_pixel_errors_zero_on_exact_geometry() -> None:
    groups = _groups(2)
    tri = triangulate_groups(groups, POSES)
    models = {
        name: CameraModel(name=name, matrix=K, distortions=DIST) for name in tri.camera_order
    }
    per_camera, overall = pixel_errors(
        tri.camera_order, POSES, tri.points3d, tri.obs_camera, tri.obs_point, tri.obs_px, models
    )
    assert overall < 1e-6
    assert all(error < 1e-6 for error in per_camera.values())


def test_sweep_orchestration_solves_from_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Timestamp sidecars on disk; video decoding is replaced by synthetic
    # detections (the video round-trip itself is covered in the recorder tests).
    cameras = list(POSES)
    fps = 30.0
    for name in cameras:
        stamps = [f"{g / fps + 0.001:.6f}" for g in range(6)]
        (tmp_path / f"{name}.timestamps").write_text("\n".join(stamps) + "\n")

    groups = _groups(6)

    def fake_detect(
        directory: Path,
        groups_frames: list[dict[str, int]],
        models: dict[str, CameraModel],
        board: CalibrationBoard,
    ) -> list[dict[str, GroupDetection]]:
        assert len(groups_frames) == 6  # all groups synchronized + selected
        return [groups[frames[cameras[0]]] for frames in groups_frames]

    monkeypatch.setattr(
        "calibration_service.calibration.extrinsic._detect_group_frames", fake_detect
    )
    models = [CameraModel(name=name, matrix=K, distortions=DIST) for name in cameras]
    result = compute_extrinsic_from_sweep(
        tmp_path,
        BOARD,
        models,
        anchor="cam_0",
        window_s=0.95 / fps,
        min_shared=3,
    )
    assert result.cameras == ["cam_0", "cam_1", "cam_2"]
    assert np.allclose(result.rotations["cam_0"], np.zeros(3), atol=1e-9)
    assert np.allclose(result.translations["cam_0"], np.zeros(3), atol=1e-9)
    rot_1, _ = cv2.Rodrigues(POSES["cam_1"][:3, :3])
    assert np.allclose(result.rotations["cam_1"], rot_1.reshape(3), atol=1e-3)
    assert np.allclose(result.translations["cam_1"], POSES["cam_1"][:3, 3], atol=1e-3)
    assert result.error < 0.1  # px, exact synthetic data
    assert result.group_count == 6
    assert result.point_count == 6 * len(CHESS)
    # 3D review payload: every point carries its group; each group has a board quad
    # whose corners match the ground-truth board placement (Kabsch fit).
    assert len(result.points) == result.point_count
    assert set(result.point_groups) == set(range(6))
    assert len(result.board_quads) == 6
    quad = result.board_quads[0]
    assert quad is not None
    low, high = CHESS.min(axis=0), CHESS.max(axis=0)
    outline = np.array(
        [
            [low[0], low[1], 0.0],
            [high[0], low[1], 0.0],
            [high[0], high[1], 0.0],
            [low[0], high[1], 0.0],
        ]
    )
    tilt = _rot("x", 8.0 * np.sin(0.0)) @ _rot("y", 10.0 * np.cos(0.0))
    expected = outline @ tilt.T + np.array([-3.0, -2.6, 9.0])
    assert np.allclose(np.asarray(quad), expected, atol=5e-3)
