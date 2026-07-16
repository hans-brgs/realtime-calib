"""Tests for board definition: render, validation, config round-trip, API."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from numpy.typing import NDArray

from calibration_service.app import create_app
from calibration_service.board import render_board_png
from calibration_service.board.render import PX_PER_SQUARE
from calibration_service.board.validate import validate_board
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.session.config_store import load_board_config, save_board_config
from calibration_service.session.manager import SessionManager


def _charuco(**overrides: object) -> CalibrationBoard:
    params: dict[str, object] = {
        "board_type": BoardType.CHARUCO,
        "dictionary": "DICT_5X5_100",
        "columns": 8,
        "rows": 5,
    }
    params.update(overrides)
    return CalibrationBoard(**params)  # type: ignore[arg-type]


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(SessionManager(tmp_path, "default")))


def _decode(png: bytes) -> NDArray[np.uint8]:
    """Decode a rendered PNG; narrow the Optional + dtype at the cv2 boundary."""
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert image is not None
    return cast("NDArray[np.uint8]", image)


def test_render_charuco_png_dimensions() -> None:
    png = render_board_png(_charuco(), px_per_square=PX_PER_SQUARE)
    image = _decode(png)
    # width ~= columns * px + 2 * margin (margin = px/2 per side).
    assert image.shape[1] == 8 * PX_PER_SQUARE + PX_PER_SQUARE
    assert image.shape[0] == 5 * PX_PER_SQUARE + PX_PER_SQUARE


def test_render_aruco_single_marker() -> None:
    board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=7
    )
    png = render_board_png(board)
    image = _decode(png)
    # Square single marker + symmetric quiet zone.
    assert image.shape[0] == image.shape[1]


def test_validate_rejects_marker_id_out_of_range() -> None:
    with pytest.raises(ValueError, match="marker_id"):
        validate_board(
            CalibrationBoard(
                board_type=BoardType.ARUCO,
                dictionary="DICT_5X5_100",
                columns=1,
                rows=1,
                marker_id=100,
            )
        )


def test_render_inverted_is_negative() -> None:
    normal = render_board_png(_charuco())
    inverted = render_board_png(_charuco(inverted=True))
    a = _decode(normal)
    b = _decode(inverted)
    assert np.array_equal(b, 255 - a)


def test_validate_rejects_marker_ratio_ge_one() -> None:
    with pytest.raises(ValueError, match="marker_ratio"):
        validate_board(_charuco(marker_ratio=1.0))


def test_validate_rejects_dictionary_too_small() -> None:
    # A 16x16 ChArUco needs 128 markers; DICT_5X5_100 holds only 100.
    with pytest.raises(ValueError, match="larger dictionary"):
        validate_board(_charuco(columns=16, rows=16, dictionary="DICT_5X5_100"))


def test_board_config_round_trip(tmp_path: Path) -> None:
    board = _charuco(square_size_mm=42.5, marker_ratio=0.8)
    save_board_config(tmp_path, "demo", board, None)
    intrinsic, extrinsic, issues = load_board_config(tmp_path, "demo")
    assert intrinsic == board
    assert extrinsic is None
    assert issues == []


def test_board_config_omits_square_fields_for_aruco(tmp_path: Path) -> None:
    # A single-marker target has no squares: square_size_mm / marker_ratio are
    # omitted from its config.toml block (misleading otherwise) and the reload
    # falls back to the model defaults for those unused fields.
    aruco = CalibrationBoard(
        board_type=BoardType.ARUCO,
        dictionary="DICT_4X4_100",
        columns=1,
        rows=1,
        marker_id=8,
        marker_size_mm=297.6,
    )
    save_board_config(tmp_path, "demo", _charuco(), aruco)
    text = (tmp_path / "demo" / "config.toml").read_text()
    block = text.split("[extrinsic_board]")[1]
    assert "square_size_mm" not in block
    assert "marker_ratio" not in block
    _intrinsic, extrinsic, issues = load_board_config(tmp_path, "demo")
    assert extrinsic is not None
    assert extrinsic.marker_size_mm == 297.6
    assert extrinsic.marker_id == 8
    assert issues == []  # absent-by-design keys are NOT anomalies


def test_board_config_fails_loud_on_missing_required_key(tmp_path: Path) -> None:
    # A ChArUco block without its measured square is a corrupted physical scale:
    # it used to reload silently at 40 mm (ADR-0036 audit's sharpest finding).
    save_board_config(tmp_path, "demo", _charuco(), None)
    path = tmp_path / "demo" / "config.toml"
    text = "\n".join(
        line for line in path.read_text().splitlines() if "square_size_mm" not in line
    )
    path.write_text(text)

    intrinsic, _extrinsic, issues = load_board_config(tmp_path, "demo")
    assert intrinsic is None  # board to reconfigure, not a silently-rescaled world
    assert len(issues) == 1
    assert "square_size_mm" in issues[0]


def test_session_load_surfaces_board_issues(tmp_path: Path) -> None:
    # End to end: a corrupt board block -> SessionOut.issues names the boards step,
    # the board reads unconfigured, and a fresh definition clears the issue.
    client = _client(tmp_path)
    board = {"board_type": "charuco", "dictionary": "DICT_4X4_100"}
    client.post("/board", json={"target": "intrinsic", "board": board})
    path = tmp_path / "default" / "config.toml"
    text = "\n".join(
        line for line in path.read_text().splitlines() if "square_size_mm" not in line
    )
    path.write_text(text)

    fresh = _client(tmp_path)  # a service restart: reload from disk
    body = fresh.get("/session").json()
    assert body["intrinsic_board"] is None
    assert body["issues"] and body["issues"][0]["step"] == "boards"
    assert "square_size_mm" in body["issues"][0]["message"]

    fresh.post("/board", json={"target": "intrinsic", "board": board})
    assert fresh.get("/session").json()["issues"] == []


def test_define_board_advances_and_persists(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "target": "intrinsic",
        "board": {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 8, "rows": 5},
    }
    resp = client.post("/board", json=body)
    assert resp.status_code == 200
    session = resp.json()
    # Defining the intrinsic board advances to the extrinsic-board choice (not straight
    # to Camera Setup) so the extrinsic choice can't be skipped.
    assert session["step"] == "extrinsic_board_choice"
    assert session["intrinsic_board"]["columns"] == 8

    # Confirming the extrinsic choice — here inheriting (board=None) — completes Target
    # Config and unlocks Camera Setup.
    resp = client.post("/board", json={"target": "extrinsic", "board": None})
    assert resp.status_code == 200
    session = resp.json()
    assert session["step"] == "camera_setup"
    assert session["extrinsic_board"] is None

    # Persisted: a fresh manager reloads the board from config.toml.
    reloaded = SessionManager(tmp_path, "default").current()
    assert reloaded.intrinsic_board is not None
    assert reloaded.intrinsic_board.dictionary == "DICT_5X5_100"


def test_define_board_rejects_invalid(tmp_path: Path) -> None:
    resp = _client(tmp_path).post(
        "/board",
        json={
            "target": "intrinsic",
            "board": {
                "board_type": "charuco",
                "dictionary": "DICT_5X5_50",
                "columns": 12,
                "rows": 12,
            },
        },
    )
    assert resp.status_code == 422


def test_preview_returns_png(tmp_path: Path) -> None:
    resp = _client(tmp_path).post(
        "/board/preview",
        json={"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 8, "rows": 5},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_board_image_404_when_undefined(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/board/intrinsic/image.png").status_code == 404


def test_dictionaries_listed(tmp_path: Path) -> None:
    dicts = _client(tmp_path).get("/board/dictionaries").json()
    assert "DICT_5X5_100" in dicts
