"""The one OpenCV ChArUco board built from a board definition."""

from __future__ import annotations

import cv2

from calibration_service.board.dictionaries import resolve
from calibration_service.models.board import CalibrationBoard


def charuco_board(board: CalibrationBoard) -> cv2.aruco.CharucoBoard:
    """The board in square units (``squareLength = 1``) that render, detection and the
    computes share. ``legacy_pattern`` restores OpenCV's pre-4.6 layout, which differs
    for an even row count (EXP-15; Caliscope v0.11.5 sets it the same way,
    ``core/charuco.py``)."""
    cv_board = cv2.aruco.CharucoBoard(
        (board.columns, board.rows), 1.0, board.marker_ratio, resolve(board.dictionary)
    )
    cv_board.setLegacyPattern(board.legacy_pattern)
    return cv_board
