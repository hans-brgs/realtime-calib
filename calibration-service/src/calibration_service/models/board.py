"""Calibration board definition ([[calibration-board]], ADR-0020).

A board carries two kinds of parameters:

- **geometry** (renders the printable PNG): type, dictionary, columns/rows,
  marker/square ratio, inversion;
- **metric** (`*_mm`): the *measured* physical sizes the operator enters after
  printing — they carry the metric scale, not the render (ADR-0020).

Pure model (no OpenCV): rendering/validation live in ``calibration_service.board``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BoardType(StrEnum):
    """Supported calibration targets (see calibration-board entity)."""

    CHARUCO = "charuco"
    ARUCO = "aruco"


@dataclass
class CalibrationBoard:
    """Definition of a ChArUco/ArUco board.

    ``square_size_mm`` / ``marker_size_mm`` are measured after printing and carry
    the metric scale; ``marker_ratio`` drives only the rendered geometry.
    """

    board_type: BoardType
    dictionary: str  # e.g. "DICT_5X5_100" (an OpenCV predefined dictionary name)
    columns: int
    rows: int
    marker_ratio: float = 0.75  # marker/square, render-only
    square_size_mm: float = 40.0  # measured (metric scale)
    marker_size_mm: float = 30.0  # measured, or marker_ratio * square_size_mm
    inverted: bool = False
