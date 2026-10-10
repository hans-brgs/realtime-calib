"""Intrinsic calibration tests: keyframe selection (pure) + solver (capability-gated).

``cv2.calibrateCamera`` SIGILLs on some local OpenCV builds (LAPACK/CPU), which
would kill the whole pytest process; the solver test is skipped there and runs
where the solver works (Docker/CI).
"""

from __future__ import annotations

import functools
import itertools
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from calibration_service.board import render_board_png
from calibration_service.calibration import (
    calibrate_intrinsic,
    compute_intrinsic_from_video,
    select_keyframes,
)
from calibration_service.calibration.intrinsic import (
    _COVERAGE_COLS,
    _board_quads,
    _coverage_map,
    _cv_charuco_board,
    _is_well_spread,
    _orientation_bins,
    _union_coverage,
)
from calibration_service.detection import BoardDetection
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.recording import VideoRecorder


def _rendered_board_image(board: CalibrationBoard) -> NDArray[np.uint8]:
    """Render + decode the board; narrow the Optional + dtype at the cv2 boundary."""
    image = cv2.imdecode(np.frombuffer(render_board_png(board), np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return cast("NDArray[np.uint8]", image)


@functools.cache
def _solver_works() -> bool:
    probe = (
        "import cv2,numpy as np;"
        "o=np.zeros((54,3),np.float32);o[:,:2]=np.mgrid[0:9,0:6].T.reshape(-1,2);"
        "K=np.array([[500.,0,320],[0,500,240],[0,0,1]]);"
        "ps=[cv2.projectPoints(o,np.array([0.1,0.1*i,0.]),np.array([-4.,-3,18]),K,None)[0]"
        ".reshape(-1,1,2).astype('float32') for i in range(12)];"
        "cv2.calibrateCamera([o]*12,ps,(640,480),None,None)"
    )
    return subprocess.run([sys.executable, "-c", probe], capture_output=True).returncode == 0


def _detection(cx: float, cy: float, tilt: float, sharpness: float = 500.0) -> BoardDetection:
    # A small 2x3 grid of corners around (cx, cy): >= _MIN_CORNERS_FOR_CALIBRATION and
    # spread over both axes (not collinear), so it passes _is_well_spread too.
    offsets = [(0, 0), (5, 0), (10, 0), (0, 5), (5, 5), (10, 5)]
    corners = np.array([[cx + dx, cy + dy] for dx, dy in offsets], np.float32)
    return BoardDetection(
        found=True,
        corners=corners,
        ids=np.arange(len(offsets), dtype=np.int32),
        outline=None,
        board_coverage=0.4,
        sharpness=sharpness,
        tilt_deg=tilt,
    )


def test_intrinsic_result_scaled() -> None:
    from calibration_service.calibration import IntrinsicResult

    r = IntrinsicResult(
        matrix=[[1337.0, 0.0, 993.0], [0.0, 1337.0, 544.0], [0.0, 0.0, 1.0]],
        distortions=[0.1, -0.05, 0.0, 0.0, 0.0],
        error=0.62,
        per_view_errors=[0.6, 0.7],
        grid_count=1000,
        view_count=20,
        image_size=(1920, 1080),
    )
    s = r.scaled(0.5)
    assert s.matrix[0][0] == 668.5 and s.matrix[1][1] == 668.5  # fx, fy halved
    # Pixel centres (ADR-0051): cx' = s (cx + 0.5) - 0.5, not s cx.
    assert s.matrix[0][2] == 496.25 and s.matrix[1][2] == 271.75
    assert s.matrix[2] == [0.0, 0.0, 1.0]  # homogeneous row untouched
    assert s.distortions == r.distortions  # normalised — unchanged
    assert s.image_size == (960, 540)
    assert s.error == 0.31
    assert r.scaled(1.0) is r  # no-op at native


def test_is_well_spread_rejects_collinear() -> None:
    collinear = np.array([[0, 0], [5, 0], [10, 0], [15, 0], [20, 0], [25, 0]], np.float32)
    spread = np.array([[0, 0], [5, 0], [10, 0], [0, 5], [5, 5], [10, 5]], np.float32)
    assert _is_well_spread(collinear) is False
    assert _is_well_spread(spread) is True


def test_select_keyframes_returns_all_below_cap() -> None:
    dets = [_detection(100 + i, 100, 10) for i in range(5)]
    assert len(select_keyframes(dets, (640, 480), cap=25)) == 5


def test_select_keyframes_prefers_the_sharpest_in_each_cell() -> None:
    # No absolute blur gate (ADR-0038): sharpness only decides WITHIN a diversity
    # cell. Two well-separated clusters, cap=2 -> one anchor per cluster, each cell
    # keeps its sharpest; the blurry members lose locally, not globally.
    cluster_a = [_detection(100, 100, 5, sharpness=s) for s in (10.0, 500.0, 20.0)]
    cluster_b = [_detection(540, 380, 40, sharpness=s) for s in (30.0, 40.0, 600.0)]
    picked = select_keyframes(cluster_a + cluster_b, (640, 480), cap=2)
    assert sorted(d.sharpness for d in picked) == [500.0, 600.0]


def test_select_keyframes_calibrates_a_fully_blurry_sweep() -> None:
    # A uniformly-blurry sweep still yields keyframes — no absolute gate to reject
    # them all (the former 422-with-no-recourse mode, ADR-0038).
    dets = [_detection(100 + i * 40, 100 + i * 20, 10, sharpness=3.0) for i in range(10)]
    assert len(select_keyframes(dets, (640, 480), cap=4)) == 4


def test_select_keyframes_caps_and_keeps_extremes() -> None:
    # 40 near the centre (tiny jitter = distinct poses) + 4 spread to the corners.
    dets = [_detection(320 + i * 0.5, 240, 0) for i in range(40)]
    corners = [
        _detection(20, 20, 5),
        _detection(600, 20, 40),
        _detection(20, 440, 40),
        _detection(600, 440, 5),
    ]
    picked = select_keyframes(dets + corners, (640, 480), cap=6)
    assert len(picked) == 6
    # The spread corners are diversity anchors -> their (lone) cells are kept.
    centroids = {(round(d.corners[0, 0]), round(d.corners[0, 1])) for d in picked}  # type: ignore[index]
    assert (20, 20) in centroids and (600, 440) in centroids


def test_select_keyframes_excludes_sparse_views() -> None:
    # Regression: a 4-corner detection is exactly the production crash — OpenCV's
    # "DLT algorithm needs at least 6 points... 'count' is 4" — so it must never
    # be selected, regardless of how "diverse" its tilt/position looks.
    sparse = BoardDetection(
        found=True,
        corners=np.array([[100, 100], [105, 100], [100, 105], [105, 105]], np.float32),
        ids=np.arange(4, dtype=np.int32),
        outline=None,
        board_coverage=0.1,
        sharpness=500.0,
        tilt_deg=30.0,
    )
    good = [_detection(300 + i, 200, 10) for i in range(6)]
    picked = select_keyframes([sparse, *good], (640, 480), cap=25)
    # BoardDetection's auto __eq__ compares numpy arrays element-wise (raises on
    # shape mismatch via `in`), so check by identity instead.
    assert not any(d is sparse for d in picked)
    assert len(picked) == 6


def test_compute_from_video_reads_detects_and_guards(tmp_path: Path) -> None:
    # Record 3 frames of a rendered board, then compute: detection + selection run,
    # and the < 6 usable views guard fires *before* the (SIGILL-prone) solver.
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    gray = _rendered_board_image(board)
    h, w = gray.shape[:2]
    path = tmp_path / "capture.mkv"
    with VideoRecorder(path, w, h, fps=30) as rec:
        for _ in range(3):
            rec.write(gray)
    with pytest.raises(ValueError, match="usable views"):
        compute_intrinsic_from_video(path, board, cap=25, stride=1)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_an_untrimmed_compute_reads_past_the_announced_frame_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A variable-rate MKV (20 ms periods, one in six 24 ms) announces far fewer frames
    # than it decodes; without a trim the compute must read them all.
    from calibration_service.calibration import intrinsic as intrinsic_module

    stage = tmp_path / "frames"
    stage.mkdir()
    for i in range(40):
        cv2.imwrite(str(stage / f"{i:03d}.png"), np.full((48, 64, 3), 10 + 4 * i, np.uint8))
    path = tmp_path / "capture.mkv"
    stamps = ["-vf", "settb=1/1000,setpts=20*N+4*floor(N/6)", "-fps_mode", "passthrough"]
    source = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(stage / "%03d.png")]
    command = [*source, *stamps, "-enc_time_base", "1:1000", "-c:v", "mjpeg", str(path)]
    subprocess.run(command, check=True, capture_output=True, timeout=60)
    capture = cv2.VideoCapture(str(path))
    announced = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    assert announced < 40  # the trap: an estimate from duration x declared rate
    seen: list[int] = []

    class Recorder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def detect(self, frame: NDArray[np.uint8]) -> BoardDetection:
            seen.append(round((float(frame.mean()) - 10) / 4))
            return BoardDetection(False, None, None, None, 0.0, 0.0, None)

    monkeypatch.setattr(intrinsic_module, "BoardDetector", Recorder)
    monkeypatch.setattr(intrinsic_module, "select_keyframes", lambda d, s, cap: d)
    monkeypatch.setattr(intrinsic_module, "calibrate_intrinsic", lambda k, b, s: None)
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    compute_intrinsic_from_video(path, board, cap=25, stride=1)
    assert seen == list(range(40))


def test_coverage_map_accumulates_quad_hulls() -> None:
    # Aspect-derived rows, ~square cells; the map counts overlapping keyframe quads.
    quad = np.array([[0.0, 0.0], [639.0, 0.0], [639.0, 479.0], [0.0, 479.0]], np.float32)
    cmap = _coverage_map([quad, quad], (640, 480))
    assert len(cmap) == round(_COVERAGE_COLS * 480 / 640)  # 72 rows at 4:3
    assert all(len(row) == _COVERAGE_COLS for row in cmap)
    # Two identical full-frame quads -> the interior is covered twice.
    assert cmap[len(cmap) // 2][_COVERAGE_COLS // 2] == 2


def test_coverage_map_credits_only_the_covered_region() -> None:
    # A quad over the top-left quarter leaves the rest at zero (a real gap).
    quad = np.array([[0.0, 0.0], [319.0, 0.0], [319.0, 239.0], [0.0, 239.0]], np.float32)
    cmap = _coverage_map([quad], (640, 480))
    assert cmap[0][0] >= 1  # covered corner
    assert cmap[-1][-1] == 0  # opposite corner untouched
    # A quarter of the image is covered, read without the boundary-cell bias.
    assert _union_coverage([quad], (640, 480)) == pytest.approx(0.25, abs=0.005)


def test_union_coverage_is_an_unbiased_area_fraction() -> None:
    # INT-10: truncating then filling every touched cell overestimated a small
    # quad's area by 8 to 28 %; the cell-centre test reads its true area.
    assert _union_coverage([], (1920, 1080)) == 0.0
    full = np.array([[0.0, 0.0], [1920.0, 0.0], [1920.0, 1080.0], [0.0, 1080.0]], np.float32)
    assert _union_coverage([full], (1920, 1080)) == 1.0
    angle = np.radians(17.0)
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    side = np.sqrt(0.05 * 1920 * 1080)  # a rotated square of 5 % of the image
    square = (np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]]) * side / 2) @ rotation.T + [700, 500]
    assert _union_coverage([square.astype(np.float32)], (1920, 1080)) == pytest.approx(
        0.05, rel=0.01
    )


def test_hull_masks_match_a_whole_grid_evaluation() -> None:
    # The cells are only tested around each hull's bounding box: the masks must be
    # those of the whole grid, boundary cells included, for hulls of any size.
    from calibration_service.calibration.intrinsic import _hull_masks

    rng = np.random.default_rng(6)
    size = (1920, 1080)
    hulls = []
    for _ in range(60):
        centre = rng.uniform([0, 0], size)
        hulls.append((centre + rng.normal(0, rng.uniform(5, 400), (6, 2))).astype(np.float32))
    cols = 96
    rows = round(cols * size[1] / size[0])
    grid_x, grid_y = np.meshgrid(
        (np.arange(cols) + 0.5) * size[0] / cols, (np.arange(rows) + 0.5) * size[1] / rows
    )
    for pts, mask in zip(hulls, _hull_masks(hulls, size, cols), strict=True):
        hull = cv2.convexHull(pts).reshape(-1, 2).astype(np.float64)
        nxt = np.roll(hull, -1, axis=0)
        sign = np.sign(np.sum(hull[:, 0] * nxt[:, 1] - nxt[:, 0] * hull[:, 1]))
        whole = np.full((rows, cols), sign != 0)
        for (x0, y0), (x1, y1) in zip(hull, nxt, strict=True):
            whole &= sign * ((x1 - x0) * (grid_y - y0) - (y1 - y0) * (grid_x - x0)) >= 0
        assert np.array_equal(mask, whole)


def test_orientation_bins_drops_frontal_and_counts_azimuth() -> None:
    frontal = np.zeros(3)  # identity -> normal [0,0,1], tilt 0 -> dropped
    tilt_x = np.array([np.radians(20), 0.0, 0.0])  # tilts the normal along -y
    tilt_y = np.array([0.0, np.radians(20), 0.0])  # tilts the normal along +x
    assert _orientation_bins([frontal, tilt_x, tilt_y]) == 2


def test_board_quads_place_the_outline_at_the_pose() -> None:
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    cv_board = _cv_charuco_board(board)
    quads = _board_quads([np.zeros(3)], [np.array([0.0, 0.0, 10.0])], cv_board)
    assert len(quads) == 1
    assert len(quads[0]) == 4
    # identity rotation + translate +10 in z -> every outline corner sits at z = 10.
    assert all(abs(point[2] - 10.0) < 1e-6 for point in quads[0])


def test_compute_trim_past_the_recording_finds_no_frames(tmp_path: Path) -> None:
    # ADR-0022: a frame_start beyond the sweep trims everything -> nothing to detect.
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    gray = _rendered_board_image(board)
    h, w = gray.shape[:2]
    path = tmp_path / "capture.mkv"
    with VideoRecorder(path, w, h, fps=30) as rec:
        for _ in range(3):
            rec.write(gray)
    with pytest.raises(ValueError, match="no readable frames"):
        compute_intrinsic_from_video(path, board, cap=25, stride=1, frame_start=10)


def _projected_views() -> tuple[CalibrationBoard, list[BoardDetection]]:
    """15 noise-free views of a 7x8 ChArUco through a 600 px pinhole at 640x480."""
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    objp = np.asarray(_cv_charuco_board(board).getChessboardCorners(), np.float32)
    objp = objp - objp.mean(0)
    ids = np.arange(objp.shape[0], dtype=np.int32)
    w, h = 640, 480
    k_true = np.array([[600.0, 0, 320], [0, 600, 240], [0, 0, 1]])
    rng = np.random.default_rng(3)
    dets: list[BoardDetection] = []
    while len(dets) < 15:
        rvec = rng.uniform(-0.5, 0.5, 3)
        tvec = np.array([rng.uniform(-2, 2), rng.uniform(-1.5, 1.5), rng.uniform(12, 20)])
        proj, _ = cv2.projectPoints(objp, rvec, tvec, k_true, None)
        pts = proj.reshape(-1, 2).astype(np.float32)
        if pts[:, 0].min() < 8 or pts[:, 0].max() > w - 8:
            continue
        if pts[:, 1].min() < 8 or pts[:, 1].max() > h - 8:
            continue
        dets.append(BoardDetection(True, pts, ids.copy(), None, 0.5, 500.0, 10.0))
    return board, dets


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_calibrate_recovers_intrinsics() -> None:
    board, dets = _projected_views()
    result = calibrate_intrinsic(dets, board, (640, 480))
    assert result.error < 1.0
    assert abs(result.matrix[0][0] - 600.0) / 600.0 < 0.1  # fx within 10%
    assert result.view_count == 15


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_the_seeded_solve_alone_runs_when_it_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # ADR-0053: Caliscope v0.11.5's seed; OpenCV's own initialisation is not run.
    board, dets = _projected_views()
    solve = cv2.calibrateCameraExtended
    flags: list[int] = []

    def _solve(*args: Any, **kwargs: Any) -> Any:
        flags.append(kwargs["flags"])
        return solve(*args, **kwargs)

    monkeypatch.setattr(cv2, "calibrateCameraExtended", _solve)
    assert calibrate_intrinsic(dets, board, (640, 480)).error < 1.0
    assert flags == [cv2.CALIB_USE_INTRINSIC_GUESS]


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
@pytest.mark.parametrize("failure", ["raises", "nan"])
def test_a_failed_seeded_solve_falls_back_to_opencvs_initialisation(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    board, dets = _projected_views()
    solve = cv2.calibrateCameraExtended

    def _solve(*args: Any, **kwargs: Any) -> Any:
        if kwargs["flags"] & cv2.CALIB_USE_INTRINSIC_GUESS:
            if failure == "raises":
                raise cv2.error("(-215:Assertion failed) degenerate view")
            return (float("nan"), *solve(*args, **kwargs)[1:])
        return solve(*args, **kwargs)

    monkeypatch.setattr(cv2, "calibrateCameraExtended", _solve)
    result = calibrate_intrinsic(dets, board, (640, 480))
    assert abs(result.matrix[0][0] - 600.0) / 600.0 < 0.1
    assert np.isfinite(result.error)


def _real_keyframes() -> tuple[CalibrationBoard, list[BoardDetection], tuple[int, int]]:
    """The 50 keyframes production keeps on session test, cam_3 (fixtures/README.md)."""
    data = np.load(Path(__file__).parent / "fixtures" / "intrinsic_keyframes_test_cam3.npz")
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO,
        dictionary=str(data["dictionary"]),
        columns=int(data["columns"]),
        rows=int(data["rows"]),
        marker_ratio=float(data["marker_ratio"]),
    )
    bounds = np.cumsum(np.concatenate([[0], data["counts"]]))
    dets = [
        BoardDetection(
            True,
            data["corners"][a:b].astype(np.float32),
            data["ids"][a:b].astype(np.int32),
            None,
            0.5,
            500.0,
            10.0,
        )
        for a, b in itertools.pairwise(bounds)
    ]
    width, height = (int(v) for v in data["image_size"])
    return board, dets, (width, height)


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_the_solve_does_not_depend_on_the_order_of_the_views() -> None:
    # On these real views, OpenCV's own initialisation lands anywhere from 1.40 to
    # 6.5 px depending on their order; the seeded solve must not.
    board, dets, size = _real_keyframes()
    reference = calibrate_intrinsic(dets, board, size)
    rng = np.random.default_rng(9)
    for _ in range(2):
        order = rng.permutation(len(dets))
        shuffled = calibrate_intrinsic([dets[i] for i in order], board, size)
        assert shuffled.error == pytest.approx(reference.error, abs=1e-6)
        assert np.allclose(shuffled.matrix, reference.matrix, atol=1e-4)
        assert np.allclose(shuffled.distortions, reference.distortions, atol=1e-6)


def test_keyframes_come_back_in_frame_order() -> None:
    # The sampling's anchor order follows the tilt, so the seed: the solve takes the
    # kept views in the order the video gave them.
    rng = np.random.default_rng(4)
    dets = [
        _detection(rng.uniform(80, 560), rng.uniform(80, 400), rng.uniform(0, 40))
        for _ in range(40)
    ]
    kept = select_keyframes(dets, (640, 480), cap=10)
    positions = [next(i for i, d in enumerate(dets) if d is k) for k in kept]
    assert len(kept) == 10
    assert positions == sorted(positions)


def test_solver_failure_is_reported_as_unusable_input(monkeypatch: pytest.MonkeyPatch) -> None:
    # A cv2.error out of the solver used to escape as an HTTP 500: it is a
    # property of the views, reported like the other unusable-input cases.
    def _assert_fails(*_args: object, **_kwargs: object) -> None:
        raise cv2.error("(-215:Assertion failed) degenerate view")

    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    views = [_detection(100.0 + 40.0 * i, 100.0 + 20.0 * i, 10.0) for i in range(8)]
    monkeypatch.setattr(cv2, "calibrateCameraExtended", _assert_fails)
    with pytest.raises(ValueError, match="OpenCV calibration failed"):
        calibrate_intrinsic(views, board, (640, 480))
