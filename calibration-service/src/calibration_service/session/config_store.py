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
from calibration_service.tuning import TUNING

CONFIG_FILE = "config.toml"


def _board_to_dict(board: CalibrationBoard) -> dict[str, object]:
    data: dict[str, object] = {
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
    if board.board_type is BoardType.ARUCO:
        # A single-marker target has no squares: the scale is marker_size_mm and
        # marker_ratio is render-only for ChArUco. Serializing them here would
        # read as "square smaller than the marker" nonsense in config.toml.
        del data["square_size_mm"]
        del data["marker_ratio"]
    return data


def _board_from_dict(data: Mapping[str, Any]) -> CalibrationBoard:
    """Strict, type-aware board parse (ADR-0036 fail-loud).

    A key the board type REQUIRES that is missing raises instead of silently
    falling back — a ChArUco block without ``square_size_mm`` used to reload at
    40 mm, silently rescaling the whole world. Keys that are absent BY DESIGN for
    the type (ArUco blocks never serialize squares/ratio; ChArUco never uses
    marker_id) get neutral TUNING values their type never reads. ``inverted`` is
    a render preference: absent = not inverted, low stakes.
    """
    board_type = BoardType(data["board_type"])
    required = ["dictionary", "columns", "rows", "marker_size_mm"]
    required += ["square_size_mm", "marker_ratio"] if board_type is BoardType.CHARUCO else [
        "marker_id"
    ]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"missing required key(s) for {board_type.value}: {', '.join(missing)}")
    return CalibrationBoard(
        board_type=board_type,
        dictionary=str(data["dictionary"]),
        columns=int(data["columns"]),
        rows=int(data["rows"]),
        marker_ratio=float(data.get("marker_ratio", TUNING.board.marker_ratio)),
        marker_id=int(data.get("marker_id", TUNING.board.marker_id)),
        square_size_mm=float(data.get("square_size_mm", TUNING.board.square_size_mm)),
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
) -> tuple[CalibrationBoard | None, CalibrationBoard | None, list[str]]:
    """Read ``(intrinsic_board, extrinsic_board, issues)`` from ``config.toml``.

    Fail-loud (ADR-0036): an invalid block loads as ``None`` (board to be
    reconfigured) with an actionable message appended to ``issues`` — never a
    silently-defaulted physical scale.
    """
    path = session_dir(sessions_dir, session_id) / CONFIG_FILE
    if not path.is_file():
        return None, None, []
    issues: list[str] = []
    try:
        data = rtoml.loads(path.read_text())
    except rtoml.TomlParsingError as exc:
        return None, None, [f"config.toml is unreadable ({exc}) — reconfigure the boards"]

    def read(key: str, label: str) -> CalibrationBoard | None:
        if key not in data:
            return None
        try:
            return _board_from_dict(data[key])
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(f"the {label} board in config.toml is invalid ({exc}) — reconfigure it")
            return None

    intrinsic = read("intrinsic_board", "intrinsic")
    extrinsic = read("extrinsic_board", "extrinsic")
    return intrinsic, extrinsic, issues
