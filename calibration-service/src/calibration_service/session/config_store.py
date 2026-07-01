"""Read/write ``config.toml`` — the board definitions of a session (ADR-0016).

Kept separate from ``session.toml`` (FSM + cameras): ``config.toml`` holds the
board blocks and is what the replay/load flow derives from ([[replay-recalibration]]).
Blocks: ``[intrinsic_board]`` and, only when the operator picks a different one,
``[extrinsic_board]`` (otherwise the extrinsic board inherits the intrinsic).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import rtoml

from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.session.store import session_dir

CONFIG_FILE = "config.toml"


def _board_to_dict(board: CalibrationBoard) -> dict[str, object]:
    return {
        "board_type": board.board_type.value,
        "dictionary": board.dictionary,
        "columns": board.columns,
        "rows": board.rows,
        "marker_ratio": board.marker_ratio,
        "marker_id": board.marker_id,
        "square_size_mm": board.square_size_mm,
        "marker_size_mm": board.marker_size_mm,
        "inverted": board.inverted,
    }


def _board_from_dict(data: Mapping[str, Any]) -> CalibrationBoard:
    return CalibrationBoard(
        board_type=BoardType(data["board_type"]),
        dictionary=str(data["dictionary"]),
        columns=int(data["columns"]),
        rows=int(data["rows"]),
        marker_ratio=float(data.get("marker_ratio", 0.75)),
        marker_id=int(data.get("marker_id", 0)),
        square_size_mm=float(data["square_size_mm"]),
        marker_size_mm=float(data["marker_size_mm"]),
        inverted=bool(data.get("inverted", False)),
    )


def save_board_config(
    sessions_dir: Path,
    session_id: str,
    intrinsic: CalibrationBoard | None,
    extrinsic: CalibrationBoard | None,
) -> None:
    """Persist board blocks to ``config.toml`` atomically (temp file + rename)."""
    target = session_dir(sessions_dir, session_id)
    target.mkdir(parents=True, exist_ok=True)
    blocks: dict[str, object] = {}
    if intrinsic is not None:
        blocks["intrinsic_board"] = _board_to_dict(intrinsic)
    if extrinsic is not None:
        blocks["extrinsic_board"] = _board_to_dict(extrinsic)

    tmp = target / (CONFIG_FILE + ".tmp")
    tmp.write_text(rtoml.dumps(blocks))
    os.replace(tmp, target / CONFIG_FILE)


def load_board_config(
    sessions_dir: Path, session_id: str
) -> tuple[CalibrationBoard | None, CalibrationBoard | None]:
    """Read ``(intrinsic_board, extrinsic_board)`` from ``config.toml`` if present."""
    path = session_dir(sessions_dir, session_id) / CONFIG_FILE
    if not path.is_file():
        return None, None
    data = rtoml.loads(path.read_text())
    intrinsic = _board_from_dict(data["intrinsic_board"]) if "intrinsic_board" in data else None
    extrinsic = _board_from_dict(data["extrinsic_board"]) if "extrinsic_board" in data else None
    return intrinsic, extrinsic
