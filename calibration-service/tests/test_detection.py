"""Detection tests — closed loop with the board renderer (render -> detect)."""

from __future__ import annotations

import cv2
import numpy as np

from calibration_service.board import render_board_png
from calibration_service.detection import BoardDetector
from calibration_service.models.board import BoardType, CalibrationBoard


def _decode(png: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)


def _charuco(**overrides: object) -> CalibrationBoard:
    params: dict[str, object] = {
        "board_type": BoardType.CHARUCO,
        "dictionary": "DICT_5X5_100",
        "columns": 7,
        "rows": 8,
    }
    params.update(overrides)
    return CalibrationBoard(**params)  # type: ignore[arg-type]


def test_detects_all_charuco_corners() -> None:
    board = _charuco()
    image = _decode(render_board_png(board))
    det = BoardDetector(board).detect(image)
    assert det.found
    # A C x R ChArUco board has (C-1) x (R-1) interior corners.
    assert det.count == (7 - 1) * (8 - 1)
    # Extrapolated board outline + coverage (board fills the rendered frame).
    assert det.outline is not None and det.outline.shape == (4, 2)
    assert 0.0 < det.board_coverage <= 1.0
    assert det.sharpness > 0.0
    # A rendered board is fronto-parallel → tilt near 0.
    assert det.tilt_deg is not None and det.tilt_deg < 5.0


def test_blank_frame_not_found() -> None:
    board = _charuco()
    blank = np.full((480, 640), 255, np.uint8)
    det = BoardDetector(board).detect(blank)
    assert not det.found
    assert det.count == 0


def test_detects_single_aruco_marker() -> None:
    board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=7
    )
    image = _decode(render_board_png(board))
    det = BoardDetector(board).detect(image)
    assert det.found
    assert det.count == 4  # a single marker contributes its 4 corners
    assert det.ids is not None and set(det.ids.tolist()) == {7}


def test_wrong_marker_id_not_found() -> None:
    rendered = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=7
    )
    image = _decode(render_board_png(rendered))
    looking_for = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=42
    )
    det = BoardDetector(looking_for).detect(image)
    assert not det.found
