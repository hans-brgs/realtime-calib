"""Board parameter validation (board-generation-download, cas limites)."""

from __future__ import annotations

import math

from calibration_service.board.dictionaries import (
    dictionary_capacity,
    is_supported,
)
from calibration_service.models.board import BoardType, CalibrationBoard


def validate_board(board: CalibrationBoard) -> None:
    """Raise ``ValueError`` if the board parameters are inconsistent."""
    if not is_supported(board.dictionary):
        raise ValueError(f"unsupported dictionary: {board.dictionary!r}")
    capacity = dictionary_capacity(board.dictionary)

    if board.board_type is BoardType.ARUCO:
        # A single ArUco marker: pick a dictionary + a marker id within its capacity.
        if not 0 <= board.marker_id < capacity:
            raise ValueError(f"marker_id must be in [0, {capacity}) for {board.dictionary}")
        if board.marker_size_mm <= 0:
            raise ValueError("marker_size_mm must be > 0")
        return

    # ChArUco board.
    if board.columns < 2 or board.rows < 2:
        raise ValueError("columns and rows must both be >= 2")
    if not 0.0 < board.marker_ratio < 1.0:
        raise ValueError("marker_ratio must be in (0, 1) — the marker sits inside the square")
    if board.marker_size_mm >= board.square_size_mm:
        raise ValueError("marker_size_mm must be smaller than square_size_mm")
    # A ChArUco board fills half its cells (checkerboard) with markers.
    needed = math.ceil((board.columns * board.rows) / 2)
    if needed > capacity:
        raise ValueError(
            f"{board.columns}x{board.rows} ChArUco needs {needed} markers but "
            f"{board.dictionary} holds {capacity} — pick a larger dictionary"
        )
