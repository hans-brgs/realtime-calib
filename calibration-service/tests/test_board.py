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
    intrinsic_board = _charuco(marker_ratio=0.8)
    extrinsic_board = _charuco(marker_ratio=0.8, square_size_mm=42.5, marker_size_mm=34.0)
    save_board_config(tmp_path, "demo", intrinsic_board, extrinsic_board, inherited=True)
    intrinsic, extrinsic, inherited, issues = load_board_config(tmp_path, "demo")
    assert intrinsic == intrinsic_board
    assert extrinsic == extrinsic_board
    assert inherited is True
    assert issues == []


def test_intrinsic_block_carries_no_measurement(tmp_path: Path) -> None:
    # The measurement is the extrinsic scale and nothing else (ADR-0045): both
    # render_board_png and _cv_charuco_board build the target with
    # squareLength=1.0, so a *_mm under [intrinsic_board] is a number nothing
    # reads — and one someone would eventually believe.
    save_board_config(tmp_path, "demo", _charuco(square_size_mm=42.5), None)
    block = (tmp_path / "demo" / "config.toml").read_text().split("[intrinsic_board]")[1]
    assert "square_size_mm" not in block
    assert "marker_size_mm" not in block
    intrinsic, _extrinsic, _inherited, issues = load_board_config(tmp_path, "demo")
    assert issues == []  # absent by design for this role, not an anomaly
    assert intrinsic is not None
    assert (intrinsic.columns, intrinsic.marker_ratio) == (8, 0.75)  # geometry survives


def test_inheritance_flag_absent_for_a_separate_board(tmp_path: Path) -> None:
    # Only an inherited block is flagged: absence reads as "a board of its own".
    save_board_config(tmp_path, "demo", _charuco(), _charuco(square_size_mm=42.5))
    assert "inherited" not in (tmp_path / "demo" / "config.toml").read_text()
    _intrinsic, extrinsic, inherited, issues = load_board_config(tmp_path, "demo")
    assert extrinsic is not None and inherited is False
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
    _intrinsic, extrinsic, _inherited, issues = load_board_config(tmp_path, "demo")
    assert extrinsic is not None
    assert extrinsic.marker_size_mm == 297.6
    assert extrinsic.marker_id == 8
    assert issues == []  # absent-by-design keys are NOT anomalies


def test_board_config_fails_loud_on_missing_required_key(tmp_path: Path) -> None:
    # An EXTRINSIC ChArUco block without its measured square is a corrupted
    # physical scale: it used to reload silently at 40 mm (ADR-0036 audit's
    # sharpest finding). Required means "read by this block" (ADR-0045), and this
    # is the block that is read.
    save_board_config(tmp_path, "demo", _charuco(), _charuco(square_size_mm=42.5))
    path = tmp_path / "demo" / "config.toml"
    text = "\n".join(
        line for line in path.read_text().splitlines() if "square_size_mm" not in line
    )
    path.write_text(text)

    _intrinsic, extrinsic, _inherited, issues = load_board_config(tmp_path, "demo")
    assert extrinsic is None  # board to reconfigure, not a silently-rescaled world
    assert len(issues) == 1
    assert "square_size_mm" in issues[0]


def test_session_load_surfaces_board_issues(tmp_path: Path) -> None:
    # End to end: a corrupt board block -> SessionOut.issues names the boards step,
    # the board reads unconfigured, and a fresh definition clears the issue.
    client = _client(tmp_path)
    board = {"board_type": "charuco", "dictionary": "DICT_4X4_100"}
    client.post("/board", json={"target": "intrinsic", "board": board})
    path = tmp_path / "default" / "config.toml"
    text = "\n".join(line for line in path.read_text().splitlines() if "columns" not in line)
    path.write_text(text)

    fresh = _client(tmp_path)  # a service restart: reload from disk
    body = fresh.get("/session").json()
    assert body["intrinsic_board"] is None
    assert body["issues"] and body["issues"][0]["step"] == "boards"
    assert "columns" in body["issues"][0]["message"]

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

    # Confirming the extrinsic choice — here inheriting — completes Target Config and
    # unlocks Camera Setup. Inheriting MATERIALIZES the copy (ADR-0045): the block
    # exists on disk, carrying the intrinsic geometry and the measured size.
    resp = client.post(
        "/board",
        json={"target": "extrinsic", "board": {**body["board"], "square_size_mm": 42.5},
              "inherited": True},
    )
    assert resp.status_code == 200
    session = resp.json()
    assert session["step"] == "camera_setup"
    assert session["extrinsic_inherited"] is True
    assert session["extrinsic_board"]["columns"] == 8
    assert session["extrinsic_board"]["square_size_mm"] == 42.5

    # Persisted: a fresh manager reloads both boards, and the inheritance, from
    # config.toml — it is not re-derived by comparing geometries.
    reloaded = SessionManager(tmp_path, "default").current()
    assert reloaded.intrinsic_board is not None
    assert reloaded.intrinsic_board.dictionary == "DICT_5X5_100"
    assert reloaded.extrinsic_board is not None
    assert reloaded.extrinsic_board.square_size_mm == 42.5
    assert reloaded.extrinsic_inherited is True


def _inherit(client: TestClient, columns: int = 8, square_size_mm: float = 42.5) -> None:
    """Define a ChArUco intrinsic board, then inherit it with a measured size."""
    board = {
        "board_type": "charuco",
        "dictionary": "DICT_5X5_100",
        "columns": columns,
        "rows": 5,
    }
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post(
        "/board",
        json={
            "target": "extrinsic",
            "board": {**board, "square_size_mm": square_size_mm},
            "inherited": True,
        },
    )


def test_inherited_board_resyncs_on_intrinsic_edit(tmp_path: Path) -> None:
    # The copy must not go stale: editing the intrinsic grid after confirming the
    # inheritance re-copies the geometry, KEEPING the measured size (ADR-0045).
    # Without this the extrinsic solve would silently keep the old grid.
    client = _client(tmp_path)
    _inherit(client, columns=7, square_size_mm=42.5)
    resp = client.post(
        "/board",
        json={
            "target": "intrinsic",
            "board": {
                "board_type": "charuco",
                "dictionary": "DICT_5X5_100",
                "columns": 8,
                "rows": 5,
            },
        },
    )
    session = resp.json()
    assert session["extrinsic_board"]["columns"] == 8  # geometry followed
    assert session["extrinsic_board"]["square_size_mm"] == 42.5  # measurement kept
    assert session["extrinsic_inherited"] is True


def test_separate_extrinsic_board_does_not_resync(tmp_path: Path) -> None:
    # A board of its own is the operator's, even with an identical geometry: it
    # never follows the intrinsic one (the reason inheritance is a stored flag and
    # not a geometry comparison).
    client = _client(tmp_path)
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 5}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post("/board", json={"target": "extrinsic", "board": {**board, "square_size_mm": 45.0}})
    session = client.post(
        "/board", json={"target": "intrinsic", "board": {**board, "columns": 8}}
    ).json()
    assert session["extrinsic_inherited"] is False
    assert session["extrinsic_board"]["columns"] == 7


def test_inherited_copy_derives_the_marker_size(tmp_path: Path) -> None:
    # marker_size_mm is re-derived from the measured square, not copied from the
    # intrinsic board: validate_board rejects marker >= square, so a measurement
    # below the intrinsic block's nominal size would otherwise be refused.
    client = _client(tmp_path)
    _inherit(client, square_size_mm=20.0)  # < the 40 mm nominal default
    extrinsic = client.get("/session").json()["extrinsic_board"]
    assert extrinsic["square_size_mm"] == 20.0
    assert extrinsic["marker_size_mm"] == pytest.approx(0.75 * 20.0)


def test_derived_marker_size_is_written_rounded(tmp_path: Path) -> None:
    # config.toml is read by the operator: the derived marker size must not land
    # as 28.799999999999997 for a 38.4 mm square.
    client = _client(tmp_path)
    _inherit(client, square_size_mm=38.4)
    block = (tmp_path / "default" / "config.toml").read_text().split("[extrinsic_board]")[1]
    assert "marker_size_mm = 28.8\n" in block


def test_inherit_without_intrinsic_board_is_rejected(tmp_path: Path) -> None:
    resp = _client(tmp_path).post(
        "/board",
        json={
            "target": "extrinsic",
            "board": {"board_type": "charuco", "dictionary": "DICT_5X5_100"},
            "inherited": True,
        },
    )
    assert resp.status_code == 422


def test_session_without_extrinsic_board_is_flagged(tmp_path: Path) -> None:
    # Sessions written before ADR-0045 inherited by fallback; that fallback is
    # gone, so one claiming Target Config is done without an extrinsic block is
    # surfaced instead of calibrating on a scale nobody entered.
    client = _client(tmp_path)
    _inherit(client)
    path = tmp_path / "default" / "config.toml"
    text = path.read_text()
    path.write_text(text.split("[extrinsic_board]")[0])

    body = _client(tmp_path).get("/session").json()  # a service restart
    assert body["extrinsic_board"] is None
    assert body["issues"] and body["issues"][0]["step"] == "boards"
    assert "extrinsic board" in body["issues"][0]["message"]


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
