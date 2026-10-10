"""Pre-export checks (ADR-0057): each one's bands, inputs and failure modes."""

from __future__ import annotations

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.calibration import ExtrinsicResult
from calibration_service.calibration.extrinsic import BAInputs
from calibration_service.export import WorldFrame, run_checks, world_frame
from calibration_service.export.checks import (
    _band,
    camera_error_check,
    epipolar_check,
    frame_checks,
    rigidity_check,
)
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.session import CalibrationSession, CameraConfig, CameraStatus

# Different focals, so a check mixing the two cameras' pixels shows.
FOCALS = {"cam_0": 800.0, "cam_1": 1100.0}
CHARUCO = CalibrationBoard(
    board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=5
)
MARKER = CalibrationBoard(
    board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=2, rows=2, marker_id=8
)


def _camera(index: int, extrinsic_error: float | None = 0.3) -> CameraConfig:
    focal = FOCALS.get(f"cam_{index}", 800.0)
    return CameraConfig(
        index=index,
        name=f"cam_{index}",
        prefix="cam",
        device_path=f"/dev/v4l/by-path/cam{index}",
        device_node=f"/dev/video{index}",
        width=640,
        height=480,
        resize_factor=1.0,
        fps=30,
        status=CameraStatus.EXTRINSIC_DONE,
        matrix=[[focal, 0.0, 320.0], [0.0, focal, 240.0], [0.0, 0.0, 1.0]],
        distortions=[0.0] * 5,
        calibration_error=0.2,
        grid_count=40,
        rotation=[0.0, 0.0, 0.0],
        translation=[0.0, 0.0, 0.0],
        extrinsic_error=extrinsic_error,
    )


# Where the rig stands in the world: anything but the identity, so a check that
# assumes cam_0 at the origin shows.
WORLD_POSE = (np.array([0.3, -1.1, 0.4]), np.array([0.7, -2.0, 3.5]))


def _rig(
    noise_px: float,
    points: int = 400,
    seed: int = 3,
    world_pose: tuple[NDArray[np.float64], NDArray[np.float64]] = WORLD_POSE,
) -> tuple[CalibrationSession, ExtrinsicResult, BAInputs]:
    """Two cameras 2.5 units apart looking at points 4-6 units ahead, observed with an
    isotropic pixel noise of ``noise_px`` at each camera's focal; ``world_pose`` (a
    Rodrigues vector and a translation, world -> rig) places the rig in the world."""
    rng = np.random.default_rng(seed)
    rig = {
        "cam_0": (np.zeros(3), np.zeros(3)),
        "cam_1": (np.array([0.0, -0.3, 0.02]), np.array([-2.5, 0.05, 0.1])),
    }
    world_rotation = cv2.Rodrigues(world_pose[0])[0]
    local = np.column_stack(
        [rng.uniform(-1.5, 1.5, points), rng.uniform(-1.0, 1.0, points), rng.uniform(4, 6, points)]
    )
    world = (local - world_pose[1]) @ world_rotation  # x_world = Rw^T (x_rig - tw)
    rotations: dict[str, list[float]] = {}
    translations: dict[str, list[float]] = {}
    obs_camera: list[int] = []
    obs_point: list[int] = []
    obs_norm: list[list[float]] = []
    for c, name in enumerate(("cam_0", "cam_1")):
        rotation = cv2.Rodrigues(rig[name][0])[0] @ world_rotation
        translation = cv2.Rodrigues(rig[name][0])[0] @ world_pose[1] + rig[name][1]
        rotations[name] = [float(v) for v in cv2.Rodrigues(rotation)[0].ravel()]
        translations[name] = [float(v) for v in translation]
        seen = world @ rotation.T + translation
        noise = rng.normal(0.0, noise_px / FOCALS[name], (points, 2))
        norm = seen[:, :2] / seen[:, 2:] + noise
        for p in range(points):
            obs_camera.append(c)
            obs_point.append(p)
            obs_norm.append([float(v) for v in norm[p]])
    result = ExtrinsicResult(
        cameras=["cam_0", "cam_1"],
        rotations=rotations,
        translations=translations,
        per_camera_error={"cam_0": 0.3, "cam_1": 0.3},
        error=0.3,
        pair_errors={},
        group_count=1,
        point_count=points,
        rigidity_mm=0.4,
    )
    ba = BAInputs(obs_camera, obs_point, obs_norm, [[0.0, 0.0]] * len(obs_camera), [0] * points)
    session = CalibrationSession(session_id="rig", cameras=[_camera(0), _camera(1)])
    return session, result, ba


@pytest.mark.parametrize(("noise_px", "status"), [(0.2, "ok"), (0.8, "warn"), (2.0, "fail")])
def test_epipolar_median_reads_the_pixel_noise(noise_px: float, status: str) -> None:
    # Isotropic noise sigma per axis on both views moves a point off its epipolar line
    # by about N(0, sqrt(2) sigma) in each view's own pixels; the symmetric median comes
    # out near one sigma on this rig (0.97 to 1.11 over seeds): the check reads pixels.
    session, result, ba = _rig(noise_px)
    check = epipolar_check(session, result, ba)
    assert check.value is not None
    assert check.value == pytest.approx(noise_px, rel=0.15)
    assert check.status == status
    assert set(check.items) == {"cam_0|cam_1"}


def test_epipolar_ignores_where_the_rig_stands() -> None:
    # The same observations, the rig placed elsewhere in the world: the same distances.
    session, result, ba = _rig(0.5)
    moved = _rig(0.5, world_pose=(np.zeros(3), np.zeros(3)))
    assert epipolar_check(session, result, ba).value == pytest.approx(
        epipolar_check(*moved).value, rel=1e-9
    )


def test_epipolar_equals_opencv_epilines_in_each_views_pixels() -> None:
    # An independent computation: F = Kb^-T [t]x R Ka^-1 on pixel coordinates, the lines
    # from cv2.computeCorrespondEpilines, each distance in its own view's pixels.
    session, result, ba = _rig(0.7)
    k = {c.name: np.asarray(c.matrix, np.float64) for c in session.cameras}
    poses = {
        n: (cv2.Rodrigues(np.asarray(result.rotations[n]))[0], np.asarray(result.translations[n]))
        for n in result.cameras
    }
    rotation = poses["cam_1"][0] @ poses["cam_0"][0].T
    t = poses["cam_1"][1] - rotation @ poses["cam_0"][1]
    cross = np.array([[0.0, -t[2], t[1]], [t[2], 0.0, -t[0]], [-t[1], t[0], 0.0]])
    fundamental = np.linalg.inv(k["cam_1"]).T @ cross @ rotation @ np.linalg.inv(k["cam_0"])
    pixels: dict[int, list[NDArray[np.float64]]] = {0: [], 1: []}
    for camera, norm in zip(ba.obs_camera, ba.obs_norm, strict=True):
        pixels[camera].append(k[f"cam_{camera}"] @ np.array([norm[0], norm[1], 1.0]))
    pa, pb = (np.asarray(pixels[c])[:, :2] for c in (0, 1))

    def distances(points: NDArray[np.float64], lines: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.abs(lines[:, 0] * points[:, 0] + lines[:, 1] * points[:, 1] + lines[:, 2])

    in_b = distances(
        pb, cv2.computeCorrespondEpilines(pa.reshape(-1, 1, 2), 1, fundamental).reshape(-1, 3)
    )
    in_a = distances(
        pa, cv2.computeCorrespondEpilines(pb.reshape(-1, 1, 2), 2, fundamental).reshape(-1, 3)
    )
    expected = float(np.median(0.5 * (in_a + in_b)))
    assert epipolar_check(session, result, ba).value == pytest.approx(expected, rel=1e-6)


def test_epipolar_bands_include_their_bounds() -> None:
    assert [_band(v, (0.5, 1.0)) for v in (0.5, 0.5001, 1.0, 1.0001)] == [
        "ok",
        "warn",
        "warn",
        "fail",
    ]


def test_epipolar_sees_a_wrong_relative_pose() -> None:
    # Noise-free observations, but cam_1's solved pose off by 1 degree: the pair fails.
    session, result, ba = _rig(0.0)
    assert epipolar_check(session, result, ba).value == pytest.approx(0.0, abs=1e-6)
    rotations = dict(result.rotations)
    rotations["cam_1"] = [rotations["cam_1"][0] + np.radians(1.0), *rotations["cam_1"][1:]]
    tilted = ExtrinsicResult(**{**result.__dict__, "rotations": rotations})
    assert epipolar_check(session, tilted, ba).status == "fail"


def test_epipolar_needs_enough_shared_observations() -> None:
    session, result, ba = _rig(0.2, points=20)
    assert epipolar_check(session, result, ba).status == "ok"
    session, result, ba = _rig(0.2, points=19)
    check = epipolar_check(session, result, ba)
    assert check.status == "unavailable"
    assert "20" in check.detail
    assert epipolar_check(session, None, ba).detail == "no extrinsic solve"
    assert "recompute" in epipolar_check(session, result, None).detail


def test_camera_error_bands_on_the_worst_camera_without_falling_back() -> None:
    session = CalibrationSession(session_id="s", cameras=[_camera(0, 0.5), _camera(1, 0.9)])
    check = camera_error_check(session)
    assert (check.status, check.value, check.items) == ("warn", 0.9, {"cam_0": 0.5, "cam_1": 0.9})
    session.cameras[1].extrinsic_error = 1.3
    assert camera_error_check(session).status == "fail"
    # A camera without an extrinsic error is not judged on its intrinsic one.
    session = CalibrationSession(session_id="s", cameras=[_camera(0, None), _camera(1, None)])
    assert camera_error_check(session).status == "unavailable"


def test_rigidity_is_a_share_of_the_targets_longest_side() -> None:
    _, result, _ = _rig(0.0)
    # ChArUco: max(7, 5) squares of the default size; marker: its side.
    for board, side in ((CHARUCO, 7 * CHARUCO.square_size_mm), (MARKER, MARKER.marker_size_mm)):
        for share, status in ((0.002, "ok"), (0.005, "warn"), (0.02, "fail")):
            check = rigidity_check(
                ExtrinsicResult(**{**result.__dict__, "rigidity_mm": share * side}), board
            )
            assert check.value == pytest.approx(share)
            assert check.status == status
            assert check.detail.split(": ")[1].startswith("tests whether")
    unrecorded = ExtrinsicResult(**{**result.__dict__, "rigidity_mm": 0.0})
    assert rigidity_check(unrecorded, CHARUCO).status == "unavailable"


def _framed(quad: list[list[float]], centres: dict[str, list[float]]) -> ExtrinsicResult:
    """A result framed on group 0, cameras at ``centres`` with identity rotations."""
    return ExtrinsicResult(
        cameras=list(centres),
        rotations={n: [0.0, 0.0, 0.0] for n in centres},
        translations={n: [-v for v in c] for n, c in centres.items()},
        per_camera_error={n: 0.1 for n in centres},
        error=0.1,
        pair_errors={},
        group_count=1,
        point_count=4,
        framed_group=0,
        board_quads=[quad],
    )


# A framed marker in the solver's world (y down): corners y up on the board, so its
# corner-order normal (x cross y) is -y, the printed face up.
MARKER_QUAD = [[-0.5, 0.0, -0.5], [0.5, 0.0, -0.5], [0.5, 0.0, 0.5], [-0.5, 0.0, 0.5]]
ABOVE = {"cam_0": [0.0, -2.0, 0.0], "cam_1": [3.0, -2.5, 1.0]}


def test_a_framed_level_target_asserts_up() -> None:
    world = world_frame(_framed(MARKER_QUAD, ABOVE), MARKER, "cam_0")
    assert (world.frame, world.up, world.group, world.below) == ("target", "y", 0, ())
    assert world.target_offset_m == pytest.approx(0.0)
    assert [c.status for c in frame_checks(world)] == ["ok", "ok"]


def test_an_upside_down_world_is_seen() -> None:
    # The pre-ADR-0057 ChArUco framing: the printed face down, the cameras under the
    # floor. A ChArUco's corner order runs y down, so the same quad reads face down.
    world = world_frame(_framed(MARKER_QUAD, ABOVE), CHARUCO, "cam_0")
    assert world.up is None
    assert world.level
    assert world.below == ()
    flipped = {n: [c[0], -c[1], c[2]] for n, c in ABOVE.items()}
    world = world_frame(_framed(MARKER_QUAD, flipped), CHARUCO, "cam_0")
    posed, above = frame_checks(world)
    assert (posed.status, above.status) == ("fail", "fail")
    assert "upside down" in posed.detail
    assert world.below == ("cam_0", "cam_1")


def test_a_camera_under_a_level_target_withholds_up() -> None:
    centres = {**ABOVE, "cam_2": [1.0, 0.5, 2.0]}
    world = world_frame(_framed(MARKER_QUAD, centres), MARKER, "cam_0")
    assert world.up is None
    assert world.below == ("cam_2",)
    assert [c.status for c in frame_checks(world)] == ["ok", "fail"]


def test_a_tilted_world_is_not_level() -> None:
    angle = np.radians(5.0)
    tilt = np.array(
        [[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]]
    )
    quad = (np.asarray(MARKER_QUAD) @ tilt.T).tolist()
    world = world_frame(_framed(quad, ABOVE), MARKER, "cam_0")
    assert world.tilt_deg == pytest.approx(5.0)
    assert not world.level and world.up is None
    posed, above = frame_checks(world)
    assert (posed.status, above.status) == ("warn", "unavailable")
    # Within the 1 degree tolerance, it still is.
    small = np.radians(0.5)
    tilt = np.array(
        [[1, 0, 0], [0, np.cos(small), -np.sin(small)], [0, np.sin(small), np.cos(small)]]
    )
    quad = (np.asarray(MARKER_QUAD) @ tilt.T).tolist()
    assert world_frame(_framed(quad, ABOVE), MARKER, "cam_0").up == "y"


def test_a_refit_target_reports_its_offset() -> None:
    # A Minimize refits the target: its centre drifts off the world origin.
    quad = (np.asarray(MARKER_QUAD) + np.array([0.05, 0.0, 0.0])).tolist()
    world = world_frame(_framed(quad, ABOVE), MARKER, "cam_0")
    assert world.target_offset_m == pytest.approx(0.05 * MARKER.marker_size_mm / 1000.0)
    assert "off the origin" in frame_checks(world)[0].detail


def test_the_anchor_world_holds_only_while_the_anchor_is_at_the_origin() -> None:
    result = ExtrinsicResult(**{**_framed(MARKER_QUAD, ABOVE).__dict__, "framed_group": None})
    assert world_frame(result, MARKER, "cam_0").frame == "unknown"  # cam_0 is at y = -2
    centred = {"cam_0": [0.0, 0.0, 0.0], "cam_1": [1.0, 0.0, 0.0]}
    result = ExtrinsicResult(**{**_framed(MARKER_QUAD, centred).__dict__, "framed_group": None})
    world = world_frame(result, MARKER, "cam_0")
    assert (world.frame, world.origin, world.up) == (
        "anchor_camera",
        "optical centre of cam_0",
        None,
    )
    assert world_frame(None, MARKER, "cam_0").frame == "unknown"
    assert [c.status for c in frame_checks(world)] == ["warn", "unavailable"]


def test_a_check_that_cannot_run_does_not_block() -> None:
    # A truncated ba_inputs.json made the export fail with a 500.
    session, result, ba = _rig(0.2)
    broken = BAInputs(ba.obs_camera, ba.obs_point, ba.obs_norm[:-1], ba.obs_px, ba.point_corner)
    unframed = WorldFrame("anchor_camera", "optical centre of cam_0", None)
    checks = run_checks(session, result, broken, CHARUCO, unframed)
    assert [c.id for c in checks] == [
        "camera_error",
        "epipolar",
        "target_rigidity",
        "frame",
        "cameras_above_floor",
        "reference",
    ]
    assert checks[1].status == "unavailable"
    assert checks[1].detail.startswith("could not run")
    assert checks[0].status == "ok"
