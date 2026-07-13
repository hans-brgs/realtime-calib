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
    ExtrinsicResult,
    GroupDetection,
    PairEstimate,
    _select_quality_groups,
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
CHESS: NDArray[np.float64] = np.asarray(
    _cv_charuco_board(BOARD).getChessboardCorners(), np.float64
)
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
    offset = np.array([-3.0 + 0.9 * group, -2.6 + 0.3 * group, 9.0 + 0.5 * group], np.float64)
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


def test_select_quality_groups_keeps_sharpest_per_time_bin() -> None:
    # ADR-0033: a group is only as good as its blurriest member (min sharpness);
    # temporal bins keep the sweep diversity while the sharpest of each bin wins.
    def group(sharp_a: float, sharp_b: float) -> dict[str, GroupDetection]:
        ids = np.arange(4, dtype=np.int32)
        px = np.zeros((4, 2))
        return {
            "cam_0": GroupDetection(ids, px, px, sharpness=sharp_a),
            "cam_1": GroupDetection(ids, px, px, sharpness=sharp_b),
        }

    groups = [
        group(10, 1),  # bin 0: min = 1
        group(5, 5),  # bin 0: min = 5  -> wins
        group(9, 2),  # bin 1: min = 2
        group(8, 8),  # bin 1: min = 8  -> wins
        group(3, 3),  # bin 2: min = 3  -> wins
        group(0.5, 100),  # bin 2: min = 0.5 (one blurry view poisons the pair)
    ]
    kept = _select_quality_groups(groups, 3)
    assert kept == [groups[1], groups[3], groups[4]]
    # At or under the cap: everything is kept, order preserved.
    assert _select_quality_groups(groups, 10) == groups


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
    result, ba_inputs = compute_extrinsic_from_sweep(
        tmp_path,
        BOARD,
        models,
        anchor="cam_0",
        window_s=0.95 / fps,
        min_shared=3,
    )
    assert len(ba_inputs.obs_camera) == len(ba_inputs.obs_norm) == len(ba_inputs.obs_px)
    assert len(ba_inputs.point_corner) == result.point_count
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


def _relative(
    result_rotations: dict[str, list[float]],
    result_translations: dict[str, list[float]],
    a: str,
    b: str,
) -> np.ndarray:
    """4x4 transform a->b from a result's per-camera world->cam poses."""
    ra = cv2.Rodrigues(np.asarray(result_rotations[a]))[0]
    rb = cv2.Rodrigues(np.asarray(result_rotations[b]))[0]
    ta = np.asarray(result_translations[a])
    tb = np.asarray(result_translations[b])
    pa, pb = np.eye(4), np.eye(4)
    pa[:3, :3], pa[:3, 3] = ra, ta
    pb[:3, :3], pb[:3, 3] = rb, tb
    return pb @ np.linalg.inv(pa)


def _fixture_result() -> ExtrinsicResult:
    rotations, translations = {}, {}
    for name, pose in POSES.items():
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        rotations[name] = [float(v) for v in rvec.reshape(3)]
        translations[name] = [float(v) for v in pose[:3, 3]]
    low, high = CHESS.min(axis=0), CHESS.max(axis=0)
    outline = np.array(
        [
            [low[0], low[1], 0.0],
            [high[0], low[1], 0.0],
            [high[0], high[1], 0.0],
            [low[0], high[1], 0.0],
        ]
    )
    tilt = _rot("x", 0.0) @ _rot("y", 10.0)
    placed = outline @ tilt.T + np.array([-3.0, -2.6, 9.0])
    return ExtrinsicResult(
        cameras=list(POSES),
        rotations=rotations,
        translations=translations,
        per_camera_error={name: 0.1 for name in POSES},
        error=0.1,
        pair_errors={},
        group_count=1,
        point_count=3,
        points=[[0.0, 0.0, 10.0], [1.0, 0.0, 10.0], [0.0, 1.0, 10.0]],
        point_groups=[0, 0, 0],
        board_quads=[[[float(v) for v in c] for c in placed]],
    )


def test_reorient_preserves_relative_geometry() -> None:
    from calibration_service.calibration.extrinsic import (
        axis_rotation_transform,
        reorient_result,
    )

    result = _fixture_result()
    turned = reorient_result(result, axis_rotation_transform("z", 90.0))
    before = _relative(result.rotations, result.translations, "cam_0", "cam_1")
    after = _relative(turned.rotations, turned.translations, "cam_0", "cam_1")
    assert np.allclose(before, after, atol=1e-9)  # rigid world change: cam-cam invariant
    # Points rotate with the frame: (x, y, z) -> (-y, x, z) for +90 about z.
    assert np.allclose(turned.points[1], [0.0, 1.0, 10.0], atol=1e-9)
    assert turned.error == result.error  # reprojection quality carries over


def test_set_origin_puts_the_board_at_the_origin() -> None:
    from calibration_service.calibration.extrinsic import (
        quad_origin_transform,
        reorient_result,
    )

    result = _fixture_result()
    quad = result.board_quads[0]
    assert quad is not None
    moved = reorient_result(result, quad_origin_transform(quad))
    new_quad = moved.board_quads[0]
    assert new_quad is not None
    width = float(np.linalg.norm(np.asarray(quad[1]) - np.asarray(quad[0])))
    assert np.allclose(new_quad[0], [0.0, 0.0, 0.0], atol=1e-9)  # c0 = origin
    assert np.allclose(new_quad[1], [width, 0.0, 0.0], atol=1e-9)  # c1 on +x
    assert abs(new_quad[3][2]) < 1e-9  # board plane = z=0


def test_set_origin_at_center_anchors_the_marker_centroid() -> None:
    # Single-ArUco targets: cv2's marker frame sits at the marker CENTER, not a
    # corner — 'Set origin' must land the world origin on the quad centroid.
    from calibration_service.calibration.extrinsic import (
        quad_origin_transform,
        reorient_result,
    )

    result = _fixture_result()
    quad = result.board_quads[0]
    assert quad is not None
    moved = reorient_result(result, quad_origin_transform(quad, at_center=True))
    new_quad = moved.board_quads[0]
    assert new_quad is not None
    assert np.allclose(np.asarray(new_quad).mean(axis=0), [0.0, 0.0, 0.0], atol=1e-9)
    assert abs(new_quad[0][2]) < 1e-9  # board plane stays z=0
    # Axes still follow the quad edges: c0 -> c1 along +x.
    edge = np.asarray(new_quad[1]) - np.asarray(new_quad[0])
    assert np.allclose(edge / np.linalg.norm(edge), [1.0, 0.0, 0.0], atol=1e-9)


def test_set_ground_makes_the_board_normal_the_up_axis() -> None:
    # 'Set ground': the operator declares the board ON THE FLOOR -> its normal
    # becomes the world's up. In OpenCV terms the board must land in the y=0
    # plane with normal -y, so every export basis (canonical -y -> platform up)
    # renders the floor flat with no manual reorientation.
    from calibration_service.calibration.extrinsic import (
        quad_origin_transform,
        reorient_result,
    )

    result = _fixture_result()
    quad = result.board_quads[0]
    assert quad is not None
    moved = reorient_result(result, quad_origin_transform(quad, ground=True))
    new_quad = np.asarray(moved.board_quads[0])
    assert np.allclose(new_quad[:, 1], 0.0, atol=1e-9)  # board lies in the y=0 plane
    x = new_quad[1] - new_quad[0]
    y = new_quad[3] - new_quad[0]
    normal = np.cross(x / np.linalg.norm(x), y / np.linalg.norm(y))
    assert np.allclose(normal, [0.0, -1.0, 0.0], atol=1e-9)  # normal = canonical up
    assert np.allclose(new_quad[0], [0.0, 0.0, 0.0], atol=1e-9)  # c0 = origin (charuco)


def test_refine_preserves_a_reoriented_anchor() -> None:
    # Compute on synthetic data, reorient the world, then Minimize: the BA must
    # hold the anchor at its REORIENTED pose (not snap back to identity).
    from calibration_service.calibration.extrinsic import (
        BAInputs,
        axis_rotation_transform,
        refine_result,
        reorient_result,
    )

    groups = _groups(4)
    tri = triangulate_groups(groups, POSES)
    solved, refined = bundle_adjust(
        tri.camera_order, POSES, tri.points3d, tri.obs_camera, tri.obs_point, tri.obs_norm, "cam_0"
    )
    rotations, translations = {}, {}
    for name, pose in solved.items():
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        rotations[name] = [float(v) for v in rvec.reshape(3)]
        translations[name] = [float(v) for v in pose[:3, 3]]
    from calibration_service.calibration.extrinsic import ExtrinsicResult

    chess_ids = tri.point_corner
    result = ExtrinsicResult(
        cameras=tri.camera_order,
        rotations=rotations,
        translations=translations,
        per_camera_error={},
        error=0.0,
        pair_errors={},
        group_count=4,
        point_count=len(refined),
        points=[[float(v) for v in p] for p in refined],
        point_groups=[int(g) for g in tri.point_group],
        board_quads=[None] * 4,
    )
    ba = BAInputs(
        obs_camera=[int(v) for v in tri.obs_camera],
        obs_point=[int(v) for v in tri.obs_point],
        obs_norm=[[float(a), float(b)] for a, b in tri.obs_norm],
        obs_px=[[float(a), float(b)] for a, b in tri.obs_px],
        point_corner=[int(v) for v in chess_ids],
    )
    turned = reorient_result(result, axis_rotation_transform("y", 90.0))
    models = [CameraModel(name=n, matrix=K, distortions=DIST) for n in tri.camera_order]
    minimized = refine_result(turned, ba, models, BOARD, "cam_0")
    # Anchor pose preserved exactly (held fixed at its reoriented pose).
    assert np.allclose(minimized.rotations["cam_0"], turned.rotations["cam_0"], atol=1e-12)
    assert np.allclose(minimized.translations["cam_0"], turned.translations["cam_0"], atol=1e-12)
    # Already-optimal geometry: the other cameras stay put within tolerance.
    assert np.allclose(minimized.rotations["cam_1"], turned.rotations["cam_1"], atol=1e-4)
    assert np.allclose(minimized.translations["cam_1"], turned.translations["cam_1"], atol=1e-3)


def test_refine_filters_outlier_observations() -> None:
    # Minimize = Caliscope's filter -> optimize loop: corrupted observations must
    # be DISCARDED by the refine (RMSE collapses back to the synthetic floor and
    # the poses land on the truth), not merely tamed by the Huber loss.
    from calibration_service.calibration.extrinsic import BAInputs, refine_result

    tri = triangulate_groups(_groups(6), POSES)
    obs_norm = tri.obs_norm.copy()
    obs_px = tri.obs_px.copy()
    corrupted = [int(np.flatnonzero(tri.obs_point == p)[0]) for p in (4, 30, 71)]
    for index in corrupted:
        obs_norm[index, 0] += 0.04  # ~32 px at f=800
        obs_px[index, 0] += 32.0

    solved, refined_points = bundle_adjust(
        tri.camera_order, POSES, tri.points3d, tri.obs_camera, tri.obs_point, obs_norm, "cam_0"
    )
    models = [CameraModel(name=n, matrix=K, distortions=DIST) for n in tri.camera_order]
    per_camera, overall = pixel_errors(
        tri.camera_order,
        solved,
        refined_points,
        tri.obs_camera,
        tri.obs_point,
        obs_px,
        {m.name: m for m in models},
    )
    assert overall > 0.5  # the outliers dominate the unfiltered RMSE

    rotations: dict[str, list[float]] = {}
    translations: dict[str, list[float]] = {}
    for name, pose in solved.items():
        rvec, _ = cv2.Rodrigues(pose[:3, :3])
        rotations[name] = [float(v) for v in rvec.reshape(3)]
        translations[name] = [float(v) for v in pose[:3, 3]]
    result = ExtrinsicResult(
        cameras=tri.camera_order,
        rotations=rotations,
        translations=translations,
        per_camera_error=per_camera,
        error=overall,
        pair_errors={},
        group_count=6,
        point_count=len(refined_points),
        points=[[float(v) for v in p] for p in refined_points],
        point_groups=[int(g) for g in tri.point_group],
        board_quads=[None] * 6,
    )
    ba = BAInputs(
        obs_camera=[int(v) for v in tri.obs_camera],
        obs_point=[int(v) for v in tri.obs_point],
        obs_norm=[[float(a), float(b)] for a, b in obs_norm],
        obs_px=[[float(a), float(b)] for a, b in obs_px],
        point_corner=[int(v) for v in tri.point_corner],
    )
    minimized = refine_result(result, ba, models, BOARD, "cam_0")
    assert minimized.error < 0.05
    rot_1, _ = cv2.Rodrigues(POSES["cam_1"][:3, :3])
    assert np.allclose(minimized.rotations["cam_1"], rot_1.reshape(3), atol=1e-3)
    assert np.allclose(minimized.translations["cam_1"], POSES["cam_1"][:3, 3], atol=1e-3)


def test_derive_sweep_window_follows_recorded_cadence(tmp_path: Path) -> None:
    from calibration_service.calibration.extrinsic import derive_sweep_window

    # Real-rig regression: config said 30 fps but the effective write cadence was
    # ~18 fps (55 ms) — the window must follow the DATA, not the config.
    for name, offset in (("cam_0", 0.0), ("cam_1", 0.012)):
        stamps = "".join(f"{i * 0.055 + offset:.6f}\n" for i in range(60))
        (tmp_path / f"{name}.timestamps").write_text(stamps)
    window = derive_sweep_window(tmp_path, ["cam_0", "cam_1"])
    assert window == pytest.approx(0.95 * 0.055, rel=0.02)
    # A camera absent from the sweep is skipped, not fatal.
    assert derive_sweep_window(tmp_path, ["cam_0", "cam_9"]) > 0
    with pytest.raises(ValueError, match="no recorded timestamps"):
        derive_sweep_window(tmp_path, ["cam_9"])


MARKER_BOARD = CalibrationBoard(
    board_type=BoardType.ARUCO,
    dictionary="DICT_4X4_100",
    columns=1,
    rows=1,
    marker_id=8,
    marker_size_mm=31.5,
)


def _marker_groups(count: int = 6) -> list[dict[str, GroupDetection]]:
    from calibration_service.calibration.extrinsic import board_object_points

    marker = board_object_points(MARKER_BOARD)  # 4 canonical corners, ids 0..3
    ids = np.arange(4, dtype=np.int32)
    groups: list[dict[str, GroupDetection]] = []
    for g in range(count):
        tilt = _rot("x", 10.0 * np.sin(g * 1.1)) @ _rot("y", 12.0 * np.cos(g * 0.7))
        offset = np.array([-1.5 + 0.6 * g, -1.0 + 0.4 * g, 8.0 + 0.5 * g])
        world = marker @ tilt.T + offset
        group: dict[str, GroupDetection] = {}
        for name, pose in POSES.items():
            norm, px = _project(world, pose)
            group[name] = GroupDetection(ids=ids, corners_px=px, corners_norm=norm)
        groups.append(group)
    return groups


def test_single_marker_board_solves_pairwise_and_full_chain() -> None:
    # Real-rig regression: the extrinsic target is a single ArUco marker (4 corners
    # per view) — the solver must accept it end to end, not just ChArUco.
    from calibration_service.calibration.extrinsic import board_unit_mm

    assert board_unit_mm(MARKER_BOARD) == 31.5  # unit = marker side, not square
    groups = _marker_groups()
    pairs = stereo_pairwise(groups, MARKER_BOARD, min_shared=3)
    assert ("cam_0", "cam_1") in pairs
    pair = pairs[("cam_0", "cam_1")]
    assert np.allclose(pair.rotation, POSES["cam_1"][:3, :3], atol=1e-3)
    assert np.allclose(pair.translation, POSES["cam_1"][:3, 3], atol=1e-3)

    poses = chain_from_anchor(pairs, list(POSES), "cam_0")
    tri = triangulate_groups(groups, poses)
    solved, refined = bundle_adjust(
        tri.camera_order, poses, tri.points3d, tri.obs_camera, tri.obs_point, tri.obs_norm, "cam_0"
    )
    assert np.allclose(solved["cam_2"][:3, :3], POSES["cam_2"][:3, :3], atol=1e-3)
    assert len(refined) == 6 * 4  # every marker corner triangulated per group


def test_sweep_groups_builds_complete_instants_despite_rate_mismatch(tmp_path: Path) -> None:
    # Real-rig regression: cameras record at EFFECTIVE rates differing by 20%+
    # (frames skipped under load). Greedy head-pairing fragmented one physical
    # instant into arbitrary 2-camera groups (pairing a camera that saw the board
    # with one that didn't); nearest-neighbour matching onto the densest timeline
    # must keep instants complete.
    from calibration_service.calibration.extrinsic import sweep_groups

    # cam_1 at ~18 fps (55 ms), cam_0 and cam_2 at ~15 fps (66 ms) with offsets.
    (tmp_path / "cam_1.timestamps").write_text(
        "".join(f"{i * 0.055:.6f}\n" for i in range(40))
    )
    for name, offset in (("cam_0", 0.020), ("cam_2", 0.041)):
        (tmp_path / f"{name}.timestamps").write_text(
            "".join(f"{i * 0.066 + offset:.6f}\n" for i in range(33))
        )
    groups = sweep_groups(tmp_path, ["cam_0", "cam_1", "cam_2"], 0.055 * 0.95)
    sizes = sorted(len(g.frames) for g in groups)
    # Mostly complete instants; no flood of fragmented pairs.
    assert sizes.count(3) >= len(groups) * 0.6
    # Full coverage of the sweep, not just its first seconds.
    assert groups[-1].timestamp - groups[0].timestamp > 0.055 * 40 * 0.85
    # A camera frame is consumed at most once across all groups.
    for name in ("cam_0", "cam_1", "cam_2"):
        used = [g.frames[name].payload for g in groups if name in g.frames]
        assert len(used) == len(set(used))
