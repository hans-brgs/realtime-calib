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
from calibration_service.calibration.intrinsic import _cv_charuco_board
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
    # A small blob of corners around (cx, cy) with a couple of ids.
    corners = np.array([[cx, cy], [cx + 5, cy], [cx, cy + 5], [cx + 5, cy + 5]], np.float32)
    return BoardDetection(
        found=True,
        corners=corners,
        ids=np.arange(4, dtype=np.int32),
        outline=None,
        board_coverage=0.4,
        sharpness=sharpness,
        tilt_deg=tilt,
    )


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
