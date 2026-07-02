"""Intrinsic calibration tests: keyframe selection (pure) + solver (capability-gated).

``cv2.calibrateCamera`` SIGILLs on some local OpenCV builds (LAPACK/CPU), which
would kill the whole pytest process; the solver test is skipped there and runs
where the solver works (Docker/CI).
"""

from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from calibration_service.board import render_board_png
from calibration_service.calibration import (
    calibrate_intrinsic,
    compute_intrinsic_from_video,
    select_keyframes,
)
from calibration_service.calibration.intrinsic import (
    _COVERAGE_COLS,
    _COVERAGE_ROWS,
    _board_quads,
    _coverage_grid,
    _cv_charuco_board,
    _image_coverage,
    _is_well_spread,
    _orientation_bins,
)
from calibration_service.detection import BoardDetection
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.recording import VideoRecorder


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
    assert s.matrix[0][2] == 496.5 and s.matrix[1][2] == 272.0  # cx, cy halved
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


def test_select_keyframes_drops_blurry() -> None:
    dets = [_detection(100, 100, 10, sharpness=10.0) for _ in range(5)]  # below gate
    assert select_keyframes(dets, (640, 480), cap=25) == []


def test_select_keyframes_applies_stride() -> None:
    dets = [_detection(100 + i, 100, 10) for i in range(10)]
    assert len(select_keyframes(dets, (640, 480), cap=25, stride=2)) == 5


def test_select_keyframes_caps_and_keeps_extremes() -> None:
    # 40 clustered near the centre + 4 spread to the corners.
    dets = [_detection(320, 240, 0) for _ in range(40)]
    corners = [
        _detection(20, 20, 5),
        _detection(600, 20, 40),
        _detection(20, 440, 40),
        _detection(600, 440, 5),
    ]
    picked = select_keyframes(dets + corners, (640, 480), cap=6)
    assert len(picked) == 6
    # Farthest-point sampling must retain the spread corner views.
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
    gray = cv2.imdecode(np.frombuffer(render_board_png(board), np.uint8), cv2.IMREAD_COLOR)
    h, w = gray.shape[:2]
    path = tmp_path / "capture.mkv"
    with VideoRecorder(path, w, h, fps=30) as rec:
        for _ in range(3):
            rec.write(gray)
    with pytest.raises(ValueError, match="usable views"):
        compute_intrinsic_from_video(path, board)


def test_coverage_grid_normalises_to_the_busiest_cell() -> None:
    # All corners land in the top-left cell -> that cell is 1.0, the rest 0.
    points = [np.array([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]], np.float32)]
    grid = _coverage_grid(points, (640, 480))
    assert len(grid) == _COVERAGE_ROWS
    assert all(len(row) == _COVERAGE_COLS for row in grid)
    assert grid[0][0] == 1.0
    assert sum(v for row in grid for v in row) == 1.0  # nothing else populated


def test_coverage_grid_spreads_across_cells() -> None:
    # A corner in each opposite corner of the frame -> two populated cells, equal weight.
    points = [np.array([[1.0, 1.0]], np.float32), np.array([[639.0, 479.0]], np.float32)]
    grid = _coverage_grid(points, (640, 480))
    assert grid[0][0] == 1.0
    assert grid[_COVERAGE_ROWS - 1][_COVERAGE_COLS - 1] == 1.0


def test_image_coverage_is_hit_cells_over_5x5() -> None:
    # One corner -> 1/25; four extreme corners -> 4 distinct cells -> 4/25 (Caliscope).
    one = [np.array([[1.0, 1.0]], np.float32)]
    assert _image_coverage(one, (500, 500)) == pytest.approx(1 / 25)
    four = [np.array([[1.0, 1.0], [499.0, 1.0], [1.0, 499.0], [499.0, 499.0]], np.float32)]
    assert _image_coverage(four, (500, 500)) == pytest.approx(4 / 25)


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
    gray = cv2.imdecode(np.frombuffer(render_board_png(board), np.uint8), cv2.IMREAD_COLOR)
    h, w = gray.shape[:2]
    path = tmp_path / "capture.mkv"
    with VideoRecorder(path, w, h, fps=30) as rec:
        for _ in range(3):
            rec.write(gray)
    with pytest.raises(ValueError, match="no readable frames"):
        compute_intrinsic_from_video(path, board, frame_start=10)


@pytest.mark.skipif(not _solver_works(), reason="cv2.calibrateCamera unavailable here (SIGILL)")
def test_calibrate_recovers_intrinsics() -> None:
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
        dets.append(
            BoardDetection(True, pts, ids.copy(), None, 0.5, 500.0, 10.0)
        )

    result = calibrate_intrinsic(dets, board, (w, h))
    assert result.error < 1.0
    assert abs(result.matrix[0][0] - 600.0) / 600.0 < 0.1  # fx within 10%
    assert result.view_count == 15
