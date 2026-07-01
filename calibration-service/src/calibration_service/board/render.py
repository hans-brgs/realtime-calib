"""Render a board to a printable PNG (backend, ADR-0020).

Fixed resolution (pixels-per-square) for a crisp print — **no** physical-scale
metadata (no DPI/pHYs). The operator prints, measures a square, and enters
``square_size_mm``; that measurement is the metric scale, not this render.
"""

from __future__ import annotations

import cv2
import numpy as np

from calibration_service.board.dictionaries import resolve
from calibration_service.board.validate import validate_board
from calibration_service.models.board import BoardType, CalibrationBoard

PX_PER_SQUARE = 120  # render density for print sharpness (not a physical scale)
_MARGIN_RATIO = 0.5  # quiet zone around the board, in squares
_BORDER_BITS = 1


def render_board_png(board: CalibrationBoard, px_per_square: int = PX_PER_SQUARE) -> bytes:
    """Render ``board`` to PNG bytes. Raises ``ValueError`` on invalid params."""
    validate_board(board)
    dictionary = resolve(board.dictionary)

    margin = round(px_per_square * _MARGIN_RATIO)
    width = board.columns * px_per_square + 2 * margin
    height = board.rows * px_per_square + 2 * margin

    if board.board_type is BoardType.CHARUCO:
        cv_board: cv2.aruco.Board = cv2.aruco.CharucoBoard(
            (board.columns, board.rows), 1.0, board.marker_ratio, dictionary
        )
    else:
        # ArUco grid: each cell = 1 unit, marker occupies marker_ratio, the rest is separation.
        cv_board = cv2.aruco.GridBoard(
            (board.columns, board.rows), board.marker_ratio, 1.0 - board.marker_ratio, dictionary
        )

    image = cv_board.generateImage((width, height), marginSize=margin, borderBits=_BORDER_BITS)

    if board.inverted:
        image = cv2.bitwise_not(image)

    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("failed to encode board PNG")
    return np.asarray(buffer).tobytes()
