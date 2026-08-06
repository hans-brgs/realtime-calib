"""Read/write ``config.toml`` — the board definitions of a session (ADR-0016).

Kept separate from ``session.toml`` (FSM + cameras): ``config.toml`` holds the
board blocks and is what the replay/load flow derives from ([[replay-recalibration]]).
Blocks: ``[intrinsic_board]``, and ``[extrinsic_board]`` as soon as the extrinsic
step is validated — inheriting materializes a copy of the intrinsic geometry there
instead of leaving the block out (ADR-0045), flagged ``inherited = true``.

Each block serializes only what its role reads: the measurement (``*_mm``) lives
under ``[extrinsic_board]`` alone, since it is the extrinsic scale and nothing
else — the intrinsic solve and the PNG render both build the target on unit
squares.
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
# Session state riding in the extrinsic block: "this board was inherited from the
# intrinsic one" is a fact about the session, not a property of a printed target,
# so it stays out of CalibrationBoard — but it belongs in the block a human reads.
INHERITED_KEY = "inherited"
# Millimetre sizes are written rounded. The ChArUco marker size is DERIVED
# (marker_ratio x square), so a 38.4 mm measured square lands as
# 28.799999999999997 in a file an operator reads. Six decimals is a nanometre —
# orders of magnitude below what a caliper resolves, so the file gets tidier
# without any measurable value being lost.
_MM_DECIMALS = 6


def _board_to_dict(board: CalibrationBoard, *, carries_scale: bool) -> dict[str, object]:
    """Serialize a board, keeping only the keys its role actually reads (ADR-0045).

    ``carries_scale`` is the extrinsic role: ``board_unit_mm`` reads the
    measurement there for the export and the BA sigmas. The intrinsic board never
    reads one — both ``render_board_png`` and ``_cv_charuco_board`` build the
    target with ``squareLength=1.0`` — so a ``*_mm`` under ``[intrinsic_board]``
    is a number nothing consumes and someone would eventually believe.
    """
    data: dict[str, object] = {
        "board_type": board.board_type.value,
        "dictionary": board.dictionary,
        "columns": board.columns,
        "rows": board.rows,
        "marker_ratio": board.marker_ratio,
        "marker_id": board.marker_id,
    }
    if carries_scale:
        data["square_size_mm"] = round(board.square_size_mm, _MM_DECIMALS)
        data["marker_size_mm"] = round(board.marker_size_mm, _MM_DECIMALS)
    data["inverted"] = board.inverted
    if board.board_type is BoardType.ARUCO:
        # A single-marker target has no squares: the scale is marker_size_mm and
        # marker_ratio is render-only for ChArUco. Serializing them here would
        # read as "square smaller than the marker" nonsense in config.toml.
        data.pop("square_size_mm", None)
        del data["marker_ratio"]
    return data


def _board_from_dict(data: Mapping[str, Any], *, carries_scale: bool) -> CalibrationBoard:
    """Strict, role- and type-aware board parse (ADR-0036 fail-loud).

    A key the block REQUIRES that is missing raises instead of silently falling
    back — an extrinsic ChArUco block without ``square_size_mm`` used to reload at
    40 mm, silently rescaling the whole world. Required means "read by this
    block": the measurement is required only where ``carries_scale`` (ADR-0045).
    Keys absent BY DESIGN (ArUco blocks never serialize squares/ratio; ChArUco
    never uses marker_id; the intrinsic block carries no size) get neutral TUNING
    values nothing reads. ``inverted`` is a render preference: absent = not
    inverted, low stakes.
    """
    board_type = BoardType(data["board_type"])
    required = ["dictionary", "columns", "rows"]
    required += ["marker_ratio"] if board_type is BoardType.CHARUCO else ["marker_id"]
    if carries_scale:
        required.append("marker_size_mm")
        if board_type is BoardType.CHARUCO:
            required.append("square_size_mm")
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
        marker_size_mm=float(data.get("marker_size_mm", TUNING.board.marker_size_mm)),
        inverted=bool(data.get("inverted", False)),
    )


def save_board_config(
    sessions_dir: Path,
    session_id: str,
    intrinsic: CalibrationBoard | None,
    extrinsic: CalibrationBoard | None,
    inherited: bool = False,
) -> None:
    """Persist board blocks to ``config.toml`` atomically (temp file + rename)."""
    target = session_dir(sessions_dir, session_id)
    target.mkdir(parents=True, exist_ok=True)
    blocks: dict[str, object] = {}
    if intrinsic is not None:
        blocks["intrinsic_board"] = _board_to_dict(intrinsic, carries_scale=False)
    if extrinsic is not None:
        # Flag first: it frames the block for whoever opens the file.
        block: dict[str, object] = {INHERITED_KEY: True} if inherited else {}
        block.update(_board_to_dict(extrinsic, carries_scale=True))
        blocks["extrinsic_board"] = block

    tmp = target / (CONFIG_FILE + ".tmp")
    tmp.write_text(rtoml.dumps(blocks))
    os.replace(tmp, target / CONFIG_FILE)


def load_board_config(
    sessions_dir: Path, session_id: str
) -> tuple[CalibrationBoard | None, CalibrationBoard | None, bool, list[str]]:
    """Read ``(intrinsic_board, extrinsic_board, inherited, issues)`` from ``config.toml``.

    Fail-loud (ADR-0036): an invalid block loads as ``None`` (board to be
    reconfigured) with an actionable message appended to ``issues`` — never a
    silently-defaulted physical scale.
    """
    path = session_dir(sessions_dir, session_id) / CONFIG_FILE
    if not path.is_file():
        return None, None, False, []
    issues: list[str] = []
    try:
        data = rtoml.loads(path.read_text())
    except rtoml.TomlParsingError as exc:
        return None, None, False, [f"config.toml is unreadable ({exc}) — reconfigure the boards"]

    def read(key: str, label: str, *, carries_scale: bool) -> CalibrationBoard | None:
        if key not in data:
            return None
        block = data[key]
        if isinstance(block, Mapping):
            # Strip the session-level flag so the board parse only ever sees board
            # keys — it is not a CalibrationBoard field (ADR-0045).
            block = {k: v for k, v in block.items() if k != INHERITED_KEY}
        try:
            return _board_from_dict(block, carries_scale=carries_scale)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(f"the {label} board in config.toml is invalid ({exc}) — reconfigure it")
            return None

    intrinsic = read("intrinsic_board", "intrinsic", carries_scale=False)
    extrinsic = read("extrinsic_board", "extrinsic", carries_scale=True)
    # Only a block that parsed can be flagged inherited; a rejected one is already
    # reported as an issue and the step has to be redone anyway.
    inherited = extrinsic is not None and bool(data["extrinsic_board"].get(INHERITED_KEY, False))
    return intrinsic, extrinsic, inherited, issues
