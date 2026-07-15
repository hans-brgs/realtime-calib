"""Served pipeline defaults, their resolution and the API bounds (ADR-0036).

GET /defaults is the single source the webapp seeds from; omitted request fields
resolve against TUNING in the transport layer; Pydantic bounds make the API
defend itself without the webapp's NumberInput clamps.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.calibration.extrinsic import BAInputs, ExtrinsicResult
from calibration_service.calibration.intrinsic import IntrinsicResult
from calibration_service.recording import VideoRecorder
from calibration_service.session.manager import SessionManager
from calibration_service.transport import api as api_module
from calibration_service.tuning import TUNING


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(SessionManager(tmp_path, "default")))


def test_get_defaults_serves_the_tuning_object(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/defaults").json()

    # Same keys as the dataclass (tuples arrive as JSON arrays), and the values
    # the whole webapp seeds from.
    assert set(body) == set(asdict(TUNING))
    assert body["fps_options"] == [30, 15]
    assert body["export_units"] == "m"
    assert body["intrinsic_stride"] == TUNING.intrinsic_stride
    assert body["intrinsic_cap_bounds"] == [6, 100]
    assert body["extrinsic_stride_charuco"] == 12
    assert body["extrinsic_stride_marker"] == 2
    assert body["board"]["columns"] == 7
    assert body["board"]["rows"] == 9
    assert body["board"]["dictionary"] == "DICT_4X4_100"


def _intrinsic_result() -> IntrinsicResult:
    return IntrinsicResult(
        matrix=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        distortions=[0.0] * 8,
        error=0.12,
        per_view_errors=[0.1] * 6,
        grid_count=42,
        view_count=6,
        image_size=(64, 48),
        coverage=((0.0, 1.0),),
        image_coverage=0.5,
        orientation_bins=6,
        board_quads=(),
    )


def _one_camera_client(tmp_path: Path) -> tuple[TestClient, SessionManager]:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_4X4_100"}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [
                {
                    "index": 0,
                    "device_path": "/dev/v4l/by-path/cam0",
                    "device_node": "/dev/video0",
                    "width": 64,
                    "height": 48,
                    "fps": 30,
                }
            ],
        },
    )
    with VideoRecorder(manager.intrinsic_video_path("cam_0"), 64, 48, fps=30) as rec:
        for _ in range(3):
            rec.write(np.zeros((48, 64, 3), dtype=np.uint8))
    return client, manager


def test_intrinsic_compute_resolves_omitted_knobs_from_tuning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _manager = _one_camera_client(tmp_path)
    captured: dict[str, object] = {}

    def fake_compute(*_args: object, **kwargs: object) -> IntrinsicResult:
        captured.update(kwargs)
        return _intrinsic_result()

    monkeypatch.setattr(api_module, "compute_intrinsic_from_video", fake_compute)

    assert client.post("/intrinsic/cam_0/compute").status_code == 200
    assert captured["cap"] == TUNING.intrinsic_cap
    assert captured["stride"] == TUNING.intrinsic_stride

    captured.clear()
    body = {"stride": 3, "cap": 40}
    assert client.post("/intrinsic/cam_0/compute", json=body).status_code == 200
    assert captured["cap"] == 40  # explicit values are honoured verbatim
    assert captured["stride"] == 3


def _extrinsic_ready_client(tmp_path: Path) -> tuple[TestClient, SessionManager]:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_4X4_100"}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [
                {
                    "index": i,
                    "device_path": f"/dev/v4l/by-path/cam{i}",
                    "device_node": f"/dev/video{i}",
                    "width": 64,
                    "height": 48,
                    "fps": 30,
                }
                for i in range(2)
            ],
        },
    )
    for camera in manager.current().cameras:
        camera.matrix = [[600.0, 0.0, 32.0], [0.0, 600.0, 24.0], [0.0, 0.0, 1.0]]
        camera.distortions = [0.0] * 8
    directory = manager.extrinsic_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text('{"cameras": []}')
    for name in ("cam_0", "cam_1"):
        (directory / f"{name}.timestamps").write_text("0.000000\n0.033000\n0.066000\n")
    return client, manager


def test_extrinsic_compute_resolves_board_type_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _manager = _extrinsic_ready_client(tmp_path)
    captured: dict[str, object] = {}
    fixture = ExtrinsicResult(
        cameras=["cam_0", "cam_1"],
        rotations={"cam_0": [0.0, 0.0, 0.0], "cam_1": [0.0, 0.0, 0.0]},
        translations={"cam_0": [0.0, 0.0, 0.0], "cam_1": [1.0, 0.0, 0.0]},
        per_camera_error={"cam_0": 0.2, "cam_1": 0.3},
        error=0.25,
        pair_errors={},
        group_count=1,
        point_count=1,
    )
    ba_fixture = BAInputs(
        obs_camera=[0, 1],
        obs_point=[0, 0],
        obs_norm=[[0.1, 0.2], [0.3, 0.4]],
        obs_px=[[10.0, 20.0], [30.0, 40.0]],
        point_corner=[0],
    )

    def fake_compute(*_args: object, **kwargs: object) -> tuple[ExtrinsicResult, BAInputs]:
        captured.update(kwargs)
        return fixture, ba_fixture

    monkeypatch.setattr(api_module, "compute_extrinsic_from_sweep", fake_compute)

    # Empty body: the ChArUco board-type defaults apply.
    assert client.post("/extrinsic/compute").status_code == 200
    assert captured["stride"] == TUNING.extrinsic_stride_charuco
    assert captured["max_groups"] == TUNING.max_groups_charuco
    assert captured["min_shared"] == TUNING.min_shared
    assert captured["max_spread_s"] is None

    # Explicit knobs are honoured verbatim (incl. the ms -> s conversion).
    captured.clear()
    body = {"stride": 4, "max_groups": 120, "max_spread_ms": 12.0, "min_shared": 3}
    assert client.post("/extrinsic/compute", json=body).status_code == 200
    assert captured["stride"] == 4
    assert captured["max_groups"] == 120
    assert captured["min_shared"] == 3
    assert captured["max_spread_s"] == pytest.approx(0.012)


def test_bounds_reject_out_of_range_values(tmp_path: Path) -> None:
    client = _client(tmp_path)

    def config(fps: int, resize: float = 1.0) -> int:
        return client.post(
            "/cameras/config",
            json={
                "prefix": "cam",
                "cameras": [
                    {
                        "index": 0,
                        "device_path": "p",
                        "device_node": "n",
                        "width": 64,
                        "height": 48,
                        "resize_factor": resize,
                        "fps": fps,
                    }
                ],
            },
        ).status_code

    assert config(fps=60) == 422  # above the ladder cap (ADR-0037)
    assert config(fps=30, resize=1.5) == 422  # resize_factor is (0, 1]
    assert config(fps=30, resize=-1.0) == 422

    # Body validation fires before any session/recording lookup.
    assert client.post("/intrinsic/cam_0/compute", json={"stride": 0}).status_code == 422
    assert client.post("/intrinsic/cam_0/compute", json={"cap": 5000}).status_code == 422
    assert client.post("/extrinsic/compute", json={"max_groups": 5000}).status_code == 422
    assert client.post("/extrinsic/compute", json={"max_spread_ms": 0}).status_code == 422
    assert client.post("/extrinsic/compute", json={"min_shared": 0}).status_code == 422


def test_export_config_requires_explicit_units(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/export/config", json={"formats": ["caliscope"]}).status_code == 422


def test_board_rejects_single_marker_for_intrinsic(tmp_path: Path) -> None:
    client = _client(tmp_path)
    board = {"board_type": "aruco", "dictionary": "DICT_4X4_100"}
    response = client.post("/board", json={"target": "intrinsic", "board": board})
    assert response.status_code == 422
    assert "ChArUco" in response.json()["detail"]


def test_board_omitted_fields_default_to_tuning(tmp_path: Path) -> None:
    client = _client(tmp_path)
    board = {"board_type": "charuco", "dictionary": "DICT_4X4_100"}
    assert client.post("/board", json={"target": "intrinsic", "board": board}).status_code == 200

    served = client.get("/session").json()["intrinsic_board"]
    assert served["columns"] == TUNING.board.columns
    assert served["rows"] == TUNING.board.rows
    assert served["marker_ratio"] == TUNING.board.marker_ratio
    assert served["square_size_mm"] == TUNING.board.square_size_mm
