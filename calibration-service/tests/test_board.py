"""Tests for board definition: render, validation, config round-trip, API."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

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
    return TestClient(create_app(SessionManager(tmp_path)))


def test_render_charuco_png_dimensions() -> None:
    png = render_board_png(_charuco(), px_per_square=PX_PER_SQUARE)
    image = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    # width ~= columns * px + 2 * margin (margin = px/2 per side).
    assert image.shape[1] == 8 * PX_PER_SQUARE + PX_PER_SQUARE
    assert image.shape[0] == 5 * PX_PER_SQUARE + PX_PER_SQUARE


def test_render_aruco_single_marker() -> None:
    board = CalibrationBoard(
        board_type=BoardType.ARUCO, dictionary="DICT_5X5_100", columns=1, rows=1, marker_id=7
    )
    png = render_board_png(board)
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
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
    a = cv2.imdecode(np.frombuffer(normal, np.uint8), cv2.IMREAD_GRAYSCALE)
    b = cv2.imdecode(np.frombuffer(inverted, np.uint8), cv2.IMREAD_GRAYSCALE)
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
    intrinsic, extrinsic = load_board_config(tmp_path, "demo")
    assert intrinsic == board
    assert extrinsic is None


def test_define_board_advances_and_persists(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = {
        "target": "intrinsic",
        "board": {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 8, "rows": 5},
    }
    resp = client.post("/board", json=body)
    assert resp.status_code == 200
    session = resp.json()
    assert session["step"] == "camera_setup"  # board-first: intrinsic board unlocks cameras
    assert session["intrinsic_board"]["columns"] == 8

    # Persisted: a fresh manager reloads the board from config.toml.
    reloaded = SessionManager(tmp_path).current()
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
