"""Calibration export: Caliscope TOML round-trip + variant math (spec calibration-export)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import rtoml
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.export import (
    aniposelib_document,
    caliscope_document,
    platform_variant,
)
from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
)
from calibration_service.session.manager import SessionManager

SQUARE_MM = 40.0
K = [[800.0, 0.0, 320.0], [0.0, 800.0, 240.0], [0.0, 0.0, 1.0]]
DIST = [0.01, -0.02, 0.0, 0.0, 0.001, 0.0, 0.0, 0.0]


def _camera(index: int, rotation: list[float], translation: list[float]) -> CameraConfig:
    return CameraConfig(
        index=index,
        name=f"cam_{index}",
        prefix="cam",
        device_path=f"/dev/v4l/by-path/cam{index}",
        device_node=f"/dev/video{index}",
        width=1280,
        height=960,
        resize_factor=0.5,
        fps=30,
        status=CameraStatus.EXTRINSIC_DONE,
        matrix=K,
        distortions=DIST,
        calibration_error=0.2,
        grid_count=400,
        rotation=rotation,
        translation=translation,
        extrinsic_error=0.3,
    )


def _session() -> CalibrationSession:
    # cam_1: 90 deg about y (Rodrigues [0, pi/2, 0]), translated 2 squares along x.
    return CalibrationSession(
        session_id="demo",
        cameras=[
            _camera(0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]),
            _camera(1, [0.0, float(np.pi / 2), 0.0], [2.0, 0.0, 0.0]),
        ],
    )


def test_caliscope_document_round_trips_with_mm_translation() -> None:
    document = caliscope_document(_session(), SQUARE_MM)
    parsed = rtoml.loads(rtoml.dumps(document))
    cam_1 = parsed["cam_1"]
    assert cam_1["port"] == 1
    assert cam_1["size"] == [640, 480]  # output resolution (resize_factor 0.5)
    assert cam_1["matrix"] == K
    assert cam_1["distortions"] == DIST  # rational coefficients as calibrated
    assert cam_1["rotation"] == pytest.approx([0.0, np.pi / 2, 0.0])
    assert cam_1["translation"] == pytest.approx([80.0, 0.0, 0.0])  # squares -> mm
    assert cam_1["error"] == 0.2
    assert parsed["cam_0"]["rotation"] == [0.0, 0.0, 0.0]  # anchor identity


def test_aniposelib_document_has_metadata_and_fisheye() -> None:
    document = aniposelib_document(_session(), SQUARE_MM, overall_error=0.31)
    assert document["metadata"] == {"adjusted": True, "error": 0.31}
    assert document["cam_0"]["fisheye"] is False
    assert document["cam_1"]["translation"] == pytest.approx([80.0, 0.0, 0.0])


def test_unity_variant_position_and_quaternion() -> None:
    variant = platform_variant(_session(), "unity", SQUARE_MM)
    convention = variant["convention"]
    assert convention["handedness"] == "left"
    # OpenCV body remapped by Unity's basis lands on Unity's native camera axes.
    assert convention["camera_forward"] == pytest.approx([0.0, 0.0, 1.0])
    assert convention["camera_up"] == pytest.approx([0.0, 1.0, 0.0])

    cam_0, cam_1 = variant["cameras"]
    assert cam_0["position"] == pytest.approx([0.0, 0.0, 0.0])
    assert cam_0["quaternion"] == pytest.approx([0.0, 0.0, 0.0, 1.0])
    # p_cv = -R^T t = (0, 0, -80) mm; Unity basis diag(1,-1,1) keeps it unchanged.
    assert cam_1["position"] == pytest.approx([0.0, 0.0, -80.0])
    # R'_c2w = M Ry(-90) M = Ry(-90): quaternion (0, -sin45, 0, cos45).
    assert cam_1["quaternion"] == pytest.approx([0.0, -np.sqrt(0.5), 0.0, np.sqrt(0.5)])
    # The mirror is carried by M ONCE: the stored rotation stays proper (det +1).
    matrix = np.asarray(cam_1["matrix"])
    assert np.linalg.det(matrix[:3, :3]) == pytest.approx(1.0)
    assert cam_1["intrinsics"]["resolution"] == [640, 480]
    # fov = 2 atan(h / (2 fy)) = 2 atan(480/1600) ~= 33.4 deg.
    assert cam_1["intrinsics"]["fov_deg"] == pytest.approx(33.4, abs=0.05)


def test_every_convention_yields_proper_rotations() -> None:
    for format_id in ("threejs", "blender", "unity", "unreal"):
        variant = platform_variant(_session(), format_id, SQUARE_MM)
        for camera in variant["cameras"]:
            matrix = np.asarray(camera["matrix"])
            assert np.linalg.det(matrix[:3, :3]) == pytest.approx(1.0), format_id


def test_export_routes_write_files_and_zip(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})

    # Extrinsics incomplete -> 422 (no camera configured yet).
    assert client.post("/export", json={"formats": []}).status_code == 422

    session = manager.current()
    session.cameras.extend(_session().cameras)
    response = client.post("/export", json={"formats": ["aniposelib", "unity"]})
    assert response.status_code == 200
    names = [f["name"] for f in response.json()["files"]]
    assert names == [
        "camera_array.toml",
        "camera_array_aniposelib.toml",
        "camera_array_unity.json",
    ]
    unity = json.loads((manager.export_dir() / "camera_array_unity.json").read_text())
    assert unity["world_units"] == "mm"
    assert unity["anchor"] == "cam_0"

    archive = client.get("/export/archive")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert sorted(bundle.namelist()) == sorted(names)

    assert client.post("/export", json={"formats": ["nope"]}).status_code == 422
    assert manager.current().step.value == "export"  # wizard advanced
