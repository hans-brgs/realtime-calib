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
    IntrinsicResult,
    _board_quads,
    _coverage_map,
    _cv_charuco_board,
    _is_well_spread,
    _orientation_bins,
    _union_coverage,
)
from calibration_service.calibration.uncertainty import (
    intrinsic_covariances,
    projection_uncertainty,
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


def test_compute_detects_exactly_the_strided_frames_of_the_trim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # INT-7: grabbing the skipped frames keeps the index exact. Frame i is a flat image
    # of grey 10 + 20i, so the detector sees which frames it got: the strided indices
    # of the trim, each with its own content, frame_end excluded (frame 11 would be the
    # next stride).
    from calibration_service.calibration import intrinsic as intrinsic_module

    path = tmp_path / "capture.mkv"
    with VideoRecorder(path, 64, 48, fps=30) as rec:
        for i in range(12):
            rec.write(np.full((48, 64, 3), 10 + 20 * i, np.uint8))
    seen: list[int] = []

    class SpyDetector:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def detect(self, frame: NDArray[np.uint8]) -> BoardDetection:
            seen.append(round((float(frame.mean()) - 10) / 20))
            return BoardDetection.empty()

    monkeypatch.setattr(intrinsic_module, "BoardDetector", SpyDetector)
    monkeypatch.setattr(intrinsic_module, "select_keyframes", lambda d, s, cap: d)
    sizes: list[tuple[int, int]] = []
    monkeypatch.setattr(intrinsic_module, "calibrate_intrinsic", lambda k, b, s: sizes.append(s))
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    compute_intrinsic_from_video(path, board, cap=25, stride=3, frame_start=2, frame_end=11)
    assert seen == [2, 5, 8]
    assert sizes == [(64, 48)]


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
    with pytest.raises(ValueError, match="OpenCV calibration failed") as raised:
        calibrate_intrinsic(views, board, (640, 480))
    assert str(raised.value).count("degenerate view") == 2  # both starts' causes


def test_cells_outside_the_model_are_none_and_kept_out_of_the_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The producer side of the fold mask: a NaN cell must reach metrics.json as null
    # (Starlette refuses NaN), stay out of both summaries, and count as unmodelled.
    import json

    import calibration_service.calibration.intrinsic as intrinsic_module

    grid = np.array([[1.0, np.nan], [2.0, 4.0]])
    monkeypatch.setattr(intrinsic_module, "intrinsic_covariances", lambda *a: (None, None))
    monkeypatch.setattr(intrinsic_module, "projection_uncertainty", lambda *a: grid)
    fields = intrinsic_module._uncertainty(
        [], [], np.eye(3), np.zeros(5), [], [], ((3, 0), (3, 0)), (64, 48)
    )
    assert fields["uncertainty"] == ((1.0, None), (2.0, 4.0))
    assert fields["uncertainty_covered_px"] == pytest.approx(np.sqrt((1.0 + 4.0) / 2))
    assert fields["uncertainty_uncovered_px"] == 4.0  # the NaN cell is not averaged in
    assert fields["uncertainty_unmodelled"] == 0.25
    json.dumps(fields, allow_nan=False)


# --- Projection uncertainty (ADR-0055) -------------------------------------------------

_UNC_SIZE = (960, 540)
_UNC_K = np.array([[700.0, 0, 480], [0, 700, 270], [0, 0, 1]])
_UNC_DIST = np.array([-0.25, 0.08, 0.001, -0.001, -0.01])


def _left_biased_views(count: int = 30) -> tuple[NDArray[np.float32], list[NDArray[np.float64]]]:
    """Noise-free views of a 7x8 ChArUco kept to the left two thirds of the frame."""
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    objp = np.asarray(_cv_charuco_board(board).getChessboardCorners(), np.float32)
    objp = objp - objp.mean(0)
    rng = np.random.default_rng(1)
    width, height = _UNC_SIZE
    views: list[NDArray[np.float64]] = []
    while len(views) < count:
        rvec = rng.uniform(-0.5, 0.5, 3)
        tvec = np.array([rng.uniform(-3.0, 0.5), rng.uniform(-1.5, 1.5), rng.uniform(9, 14)])
        projected, _ = cv2.projectPoints(objp, rvec, tvec, _UNC_K, _UNC_DIST)
        pts = projected.reshape(-1, 2)
        if pts.min() > 5 and pts[:, 0].max() < width - 5 and pts[:, 1].max() < height - 5:
            views.append(np.asarray(pts, np.float64))
    return objp, views


def _gap_to_truth(
    matrix: NDArray[np.float64],
    distortions: NDArray[np.float64],
    coverage: NDArray[np.int64],
) -> NDArray[np.float64]:
    """Per cell, how far a solved model projects the TRUE ray, rotation-compensated.

    The implied rotation is fitted on the cells >= 3 keyframes cover, as the
    prediction does: what a camera rotation absorbs is not an intrinsic error.
    """
    from scipy.optimize import least_squares  # type: ignore[import-untyped]

    width, height = _UNC_SIZE
    rows, cols = coverage.shape
    grid_x, grid_y = np.meshgrid(
        (np.arange(cols) + 0.5) * width / cols, (np.arange(rows) + 0.5) * height / rows
    )
    pixels = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    criteria = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 100, 1e-12)
    rays = cv2.undistortPointsIter(  # type: ignore[call-overload]
        pixels.reshape(-1, 1, 2), _UNC_K, _UNC_DIST, None, None, criteria
    ).reshape(-1, 2)
    points = np.column_stack([rays, np.ones(len(rays))])
    fit = (coverage >= 3).ravel()

    def residual(rvec: NDArray[np.float64]) -> NDArray[np.float64]:
        projected, _ = cv2.projectPoints(points[fit], rvec, np.zeros(3), matrix, distortions)
        return np.asarray((projected.reshape(-1, 2) - pixels[fit]).ravel(), np.float64)

    rotation = least_squares(residual, np.zeros(3)).x
    projected, _ = cv2.projectPoints(points, rotation, np.zeros(3), matrix, distortions)
    return np.asarray(np.linalg.norm(projected.reshape(-1, 2) - pixels, axis=1), np.float64)


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_projection_uncertainty_matches_the_spread_of_noisy_solves() -> None:
    # The prediction of ONE noisy solve against the actual spread of 8 noisy solves
    # around the truth (independent noise, 0.5 px): within a factor 1.6 where the
    # board went and where the model extrapolates.
    objp, views = _left_biased_views(20)
    coverage = np.asarray(_coverage_map([v.astype(np.float32) for v in views], _UNC_SIZE))
    obj = [objp.reshape(-1, 1, 3)] * len(views)
    rng = np.random.default_rng(7)
    squared: list[NDArray[np.float64]] = []
    predicted: NDArray[np.float64] | None = None
    for _ in range(8):
        img = [
            (v + rng.normal(0, 0.5, v.shape)).astype(np.float32).reshape(-1, 1, 2) for v in views
        ]
        # distCoeffs=None is valid at runtime; the cv2 stub types it as required.
        _rms, k, d, rvecs, tvecs = cv2.calibrateCamera(  # type: ignore[call-overload]
            obj, img, _UNC_SIZE, _UNC_K.copy(), None, flags=cv2.CALIB_USE_INTRINSIC_GUESS
        )[:5]
        d = np.ravel(d)[:5]
        squared.append(_gap_to_truth(k, d, coverage) ** 2)
        if predicted is None:
            covariances = intrinsic_covariances(obj, img, k, d, list(rvecs), list(tvecs))
            predicted = projection_uncertainty(covariances, k, d, coverage, _UNC_SIZE).ravel()
    assert predicted is not None
    observed = np.sqrt(np.mean(squared, axis=0))
    for mask in ((coverage >= 3).ravel(), (coverage == 0).ravel()):
        ratio = np.sqrt(np.mean(observed[mask] ** 2)) / np.sqrt(np.mean(predicted[mask] ** 2))
        assert 0.6 < ratio < 1.6


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_the_independent_covariance_is_the_one_opencv_reports() -> None:
    # sigma^2 S^-1, poses marginalised, is OpenCV's own covariance of the intrinsics:
    # same Jacobian, same degrees of freedom (2N - 9 - 6V).
    objp, views = _left_biased_views(20)
    obj = [objp.reshape(-1, 1, 3)] * len(views)
    rng = np.random.default_rng(11)
    img = [(v + rng.normal(0, 0.5, v.shape)).astype(np.float32).reshape(-1, 1, 2) for v in views]
    # distCoeffs=None is valid at runtime; the cv2 stub types it as required.
    _rms, k, d, rvecs, tvecs, std_intrinsics, *_ = cv2.calibrateCameraExtended(  # type: ignore[call-overload]
        obj, img, _UNC_SIZE, _UNC_K.copy(), None, flags=cv2.CALIB_USE_INTRINSIC_GUESS
    )
    independent, _ = intrinsic_covariances(obj, img, k, np.ravel(d)[:5], list(rvecs), list(tvecs))
    assert np.allclose(np.sqrt(np.diag(independent)), np.ravel(std_intrinsics)[:9], rtol=1e-4)


def test_the_propagation_matches_perturbations_drawn_through_the_model() -> None:
    # Intrinsics drawn from a covariance, each draw's TRUE rays projected with its own
    # best rotation: the RMS displacement per cell is the linear prediction (euclidean,
    # not per axis), where the board went and where the model extrapolates.
    objp, views = _left_biased_views(20)
    coverage = np.asarray(_coverage_map([v.astype(np.float32) for v in views], _UNC_SIZE))
    obj = [objp.reshape(-1, 1, 3)] * len(views)
    rvecs: list[NDArray[np.float64]] = []
    tvecs: list[NDArray[np.float64]] = []
    for v in views:
        _ok, rvec, tvec = cv2.solvePnP(objp, v, _UNC_K, _UNC_DIST)
        rvecs.append(np.asarray(rvec, np.float64))
        tvecs.append(np.asarray(tvec, np.float64))
    rng = np.random.default_rng(12)
    img = [(v + rng.normal(0, 0.5, v.shape)).astype(np.float32).reshape(-1, 1, 2) for v in views]
    covariance, _ = intrinsic_covariances(obj, img, _UNC_K, _UNC_DIST, rvecs, tvecs)
    truth = np.array([700.0, 700.0, 480.0, 270.0, *_UNC_DIST])
    squared = []
    for draw in rng.multivariate_normal(truth, covariance, 200):
        k = np.array([[draw[0], 0.0, draw[2]], [0.0, draw[1], draw[3]], [0.0, 0.0, 1.0]])
        squared.append(_gap_to_truth(k, draw[4:], coverage) ** 2)
    observed = np.sqrt(np.mean(squared, axis=0))
    predicted = projection_uncertainty([covariance], _UNC_K, _UNC_DIST, coverage, _UNC_SIZE).ravel()
    for mask in ((coverage >= 3).ravel(), (coverage == 0).ravel()):
        ratio = np.sqrt(np.mean(observed[mask] ** 2)) / np.sqrt(np.mean(predicted[mask] ** 2))
        assert 0.85 < ratio < 1.15


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_errors_correlated_within_a_view_need_the_robust_reading() -> None:
    # A smooth 2 px warp per view (a blur or rolling-shutter bias) on top of 0.3 px
    # noise: the independent covariance alone reads ~2.7x low where the board went;
    # the larger of the two readings, the one shipped, follows the actual spread.
    objp, views = _left_biased_views(20)
    coverage = np.asarray(_coverage_map([v.astype(np.float32) for v in views], _UNC_SIZE))
    obj = [objp.reshape(-1, 1, 3)] * len(views)
    rng = np.random.default_rng(7)
    squared: list[NDArray[np.float64]] = []
    shipped: NDArray[np.float64] | None = None
    independent: NDArray[np.float64] | None = None
    for _ in range(12):
        img = []
        for v in views:
            d = (v - v.mean(0)) / 300.0
            warp = np.column_stack([d[:, 0] ** 2, d[:, 1] ** 2, d[:, 0] * d[:, 1]])
            warp = warp @ rng.normal(0, 2.0, (2, 3)).T
            noisy = v + warp + rng.normal(0, 0.3, v.shape)
            img.append(noisy.astype(np.float32).reshape(-1, 1, 2))
        # distCoeffs=None is valid at runtime; the cv2 stub types it as required.
        _rms, k, d, rvecs, tvecs = cv2.calibrateCamera(  # type: ignore[call-overload]
            obj, img, _UNC_SIZE, _UNC_K.copy(), None, flags=cv2.CALIB_USE_INTRINSIC_GUESS
        )[:5]
        d = np.ravel(d)[:5]
        squared.append(_gap_to_truth(k, d, coverage) ** 2)
        if shipped is None:
            pair = intrinsic_covariances(obj, img, k, d, list(rvecs), list(tvecs))
            shipped = projection_uncertainty(pair, k, d, coverage, _UNC_SIZE).ravel()
            independent = projection_uncertainty([pair[0]], k, d, coverage, _UNC_SIZE).ravel()
    assert shipped is not None and independent is not None
    covered = (coverage >= 3).ravel()
    observed = np.sqrt(np.mean(np.mean(squared, axis=0)[covered]))

    def ratio(prediction: NDArray[np.float64]) -> float:
        return float(observed / np.sqrt(np.mean(prediction[covered] ** 2)))

    assert 0.6 < ratio(shipped) < 1.4
    assert ratio(independent) > 1.8


def test_cells_past_the_distortion_fold_are_outside_the_model() -> None:
    # A strong barrel lens (k ~ a 960x540 wide-angle) folds before the image corners:
    # no ray reaches them, so their cells are NaN and stay out of the rotation fit,
    # while the centre keeps its reading.
    k = np.array([[600.0, 0.0, 479.5], [0.0, 600.0, 269.5], [0.0, 0.0, 1.0]])
    distortions = np.array([-0.42, 0.25, 0.0, 0.0, -0.10])
    coverage = np.zeros((27, 48), np.int64)
    coverage[6:21, 12:36] = 3
    covariance = np.diag([4.0, 4.0, 1.0, 1.0, 1e-5, 1e-5, 1e-8, 1e-8, 1e-6])
    sigma = projection_uncertainty([covariance], k, distortions, coverage, (960, 540))
    for corner in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
        assert np.isnan(sigma[corner])
    assert np.isfinite(sigma[13, 24])
    assert np.isfinite(sigma[coverage >= 3]).all()
    assert 0.0 < float(np.mean(np.isnan(sigma))) < 0.3


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_the_solve_reports_more_uncertainty_where_the_board_never_went() -> None:
    objp, views = _left_biased_views()
    rng = np.random.default_rng(3)
    dets = [
        BoardDetection(
            True,
            (v + rng.normal(0, 0.5, v.shape)).astype(np.float32),
            np.arange(len(objp), dtype=np.int32),
            None,
            0.5,
            500.0,
            10.0,
        )
        for v in views
    ]
    board = CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )
    result = calibrate_intrinsic(dets, board, _UNC_SIZE)
    assert np.asarray(result.uncertainty).shape == np.asarray(result.coverage).shape
    assert result.uncertainty_covered_px is not None
    assert result.uncertainty_uncovered_px is not None
    assert result.uncertainty_uncovered_px > 3 * result.uncertainty_covered_px


def test_a_principal_point_shift_is_absorbed_by_the_implied_rotation() -> None:
    # A camera rotation moves every ray's projection nearly uniformly, like a cx shift:
    # a 2 px cx uncertainty must not read as a 2 px projection uncertainty.
    coverage = np.full((27, 48), 3, np.int64)
    covariance = np.zeros((9, 9))
    covariance[2, 2] = 4.0  # sigma(cx) = 2 px
    sigma = projection_uncertainty([covariance], _UNC_K, _UNC_DIST, coverage, _UNC_SIZE)
    assert sigma.max() < 0.6
    assert float(np.sqrt(np.mean(sigma**2))) < 0.3


def test_the_uncertainty_scales_to_the_output_resolution() -> None:
    result = IntrinsicResult(
        matrix=[[700.0, 0.0, 479.5], [0.0, 700.0, 269.5], [0.0, 0.0, 1.0]],
        distortions=[0.0] * 5,
        error=0.4,
        per_view_errors=[],
        grid_count=0,
        view_count=6,
        image_size=(960, 540),
        uncertainty=((1.0, 2.0), (3.0, None)),
        uncertainty_covered_px=1.0,
        uncertainty_uncovered_px=4.0,
        uncertainty_unmodelled=0.25,
    )
    half = result.scaled(0.5)
    assert half.uncertainty == ((0.5, 1.0), (1.5, None))  # outside the model stays None
    assert half.uncertainty_unmodelled == 0.25
    assert half.uncertainty_covered_px == 0.5
    assert half.uncertainty_uncovered_px == 2.0


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_a_singular_covariance_leaves_the_solve_without_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A diagnostic: a degenerate covariance must not cost the solve it describes.
    import calibration_service.calibration.intrinsic as intrinsic_module

    def _singular(*_args: Any, **_kwargs: Any) -> Any:
        raise np.linalg.LinAlgError("Singular matrix")

    monkeypatch.setattr(intrinsic_module, "intrinsic_covariances", _singular)
    board, dets = _projected_views()
    result = calibrate_intrinsic(dets, board, (640, 480))
    assert result.error < 1.0
    assert result.uncertainty == ()
    assert result.uncertainty_covered_px is None
    assert result.uncertainty_uncovered_px is None


def test_the_larger_covariance_reading_is_kept_cell_by_cell() -> None:
    coverage = np.full((27, 48), 3, np.int64)
    focal = np.zeros((9, 9))
    focal[0, 0] = focal[1, 1] = 25.0  # sigma(f) = 5 px: grows away from the centre
    radial = np.zeros((9, 9))
    radial[4, 4] = 1e-4  # sigma(k1) = 0.01: grows faster toward the corners
    one = projection_uncertainty([focal], _UNC_K, _UNC_DIST, coverage, _UNC_SIZE)
    other = projection_uncertainty([radial], _UNC_K, _UNC_DIST, coverage, _UNC_SIZE)
    both = projection_uncertainty([focal, radial], _UNC_K, _UNC_DIST, coverage, _UNC_SIZE)
    assert np.allclose(both, np.maximum(one, other))
    assert (one > other).any() and (other > one).any()  # each wins somewhere
