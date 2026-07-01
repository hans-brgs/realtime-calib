"""Burn-in overlay tests."""

from __future__ import annotations

import cv2
import numpy as np

from calibration_service.board import render_board_png
from calibration_service.detection import BoardDetection, BoardDetector
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.overlays import draw_overlay, fill_color


def _charuco() -> CalibrationBoard:
    return CalibrationBoard(
        board_type=BoardType.CHARUCO, dictionary="DICT_5X5_100", columns=7, rows=8
    )


def test_fill_color_bands() -> None:
    assert fill_color(0.05) != fill_color(0.20)  # red vs orange
    assert fill_color(0.20) != fill_color(0.40)  # orange vs yellow
    assert fill_color(0.40) != fill_color(0.80)  # yellow vs green
    assert fill_color(0.80) == fill_color(0.60)  # both green (>= 0.55)


def test_overlay_downscales_and_draws() -> None:
    board = _charuco()
    gray = cv2.imdecode(np.frombuffer(render_board_png(board), np.uint8), cv2.IMREAD_COLOR)
    detection = BoardDetector(board).detect(gray)
    assert detection.found

    out = draw_overlay(gray, detection, resize_factor=0.5)
    assert out.shape[0] == gray.shape[0] // 2
    assert out.shape[1] == gray.shape[1] // 2
    assert out.ndim == 3 and out.dtype == np.uint8
    # A copy, not the input.
    assert out.base is not gray


def test_overlay_no_detection_returns_plain_preview() -> None:
    frame = np.full((480, 640, 3), 30, np.uint8)
    out = draw_overlay(frame, BoardDetection.empty(), resize_factor=1.0)
    assert out.shape == frame.shape
    assert np.array_equal(out, frame)  # nothing drawn
    assert out is not frame  # but still a copy
