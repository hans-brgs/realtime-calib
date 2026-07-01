"""Board definition: dictionaries, validation, printable PNG rendering."""

from __future__ import annotations

from calibration_service.board.dictionaries import (
    SUPPORTED_DICTIONARIES,
    dictionary_capacity,
    is_supported,
    resolve,
)
from calibration_service.board.render import PX_PER_SQUARE, render_board_png
from calibration_service.board.validate import validate_board

__all__ = [
    "PX_PER_SQUARE",
    "SUPPORTED_DICTIONARIES",
    "dictionary_capacity",
    "is_supported",
    "render_board_png",
    "resolve",
    "validate_board",
]
