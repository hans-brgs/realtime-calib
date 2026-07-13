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
    caliscope_document,
    export_targets,
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
    # Additive id -> device reconciliation field (stable v4l path).
    assert cam_1["device_path"] == "/dev/v4l/by-path/cam1"
    assert cam_1["size"] == [640, 480]  # output resolution (resize_factor 0.5)
    assert cam_1["matrix"] == K
    assert cam_1["distortions"] == DIST  # rational coefficients as calibrated
    assert cam_1["rotation"] == pytest.approx([0.0, np.pi / 2, 0.0])
    assert cam_1["translation"] == pytest.approx([80.0, 0.0, 0.0])  # squares -> mm
    assert cam_1["error"] == 0.2
    assert parsed["cam_0"]["rotation"] == [0.0, 0.0, 0.0]  # anchor identity


def test_caliscope_document_honours_metre_units() -> None:
    # Caliscope's own arrays are metre-scaled: units="m" makes the TOML a true
    # drop-in (the units knob applies to every artifact, not just the JSONs).
    document = caliscope_document(_session(), SQUARE_MM, units="m")
    assert document["cam_1"]["translation"] == pytest.approx([0.08, 0.0, 0.0])


def test_export_targets_catalog_lists_caliscope_plus_platforms() -> None:
    # Backend = single source for the export catalog (ADR-0026): caliscope (TOML,
    # OpenCV axes) + the four platform JSONs, with display metadata.
    targets = {t.id: t for t in export_targets()}
    assert set(targets) == {"caliscope", "threejs", "blender", "unity", "unreal"}
    assert targets["caliscope"].filename == "camera_array.toml"
    assert targets["caliscope"].kind == "toml"
    assert targets["unity"].filename == "camera_array_unity.json"
    assert targets["unity"].kind == "json"
    assert targets["unity"].handedness == "left"
    assert "Unity" in targets["unity"].label and "left-handed" in targets["unity"].label


def test_unity_variant_position_and_quaternion() -> None:
    variant = platform_variant(_session(), "unity", SQUARE_MM)
    convention = variant["convention"]
    assert convention["handedness"] == "left"
    # OpenCV body remapped by Unity's basis lands on Unity's native camera axes.
    assert convention["camera_forward"] == pytest.approx([0.0, 0.0, 1.0])
    assert convention["camera_up"] == pytest.approx([0.0, 1.0, 0.0])

    cam_0, cam_1 = variant["cameras"]
    assert cam_0["device_path"] == "/dev/v4l/by-path/cam0"  # id -> device link
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


def test_view_block_only_for_right_handed_conventions() -> None:
    # RH variants carry the view form (R|t, world->camera) next to the scene form;
    # LH variants must not: R @ M^T has det=-1 there (a mirror, not a rotation).
    for format_id in ("threejs", "blender"):
        variant = platform_variant(_session(), format_id, SQUARE_MM)
        for camera in variant["cameras"]:
            r = np.asarray(camera["view"]["R"])
            t = np.asarray(camera["view"]["t"])
            assert np.linalg.det(r) == pytest.approx(1.0), format_id
            # Both forms describe the SAME pose: position = -R^T t.
            assert -r.T @ t == pytest.approx(np.asarray(camera["position"])), format_id
    for format_id in ("unity", "unreal"):
        variant = platform_variant(_session(), format_id, SQUARE_MM)
        assert all("view" not in camera for camera in variant["cameras"])


def test_units_scale_platform_world_lengths() -> None:
    mm = platform_variant(_session(), "threejs", SQUARE_MM)
    m = platform_variant(_session(), "threejs", SQUARE_MM, units="m")
    assert m["world_units"] == "m"
    cam_mm, cam_m = mm["cameras"][1], m["cameras"][1]
    assert np.asarray(cam_m["position"]) == pytest.approx(
        np.asarray(cam_mm["position"]) / 1000.0
    )
    assert np.asarray(cam_m["view"]["t"]) == pytest.approx(
        np.asarray(cam_mm["view"]["t"]) / 1000.0
    )
    # Intrinsics stay in pixels regardless of world units.
    assert cam_m["intrinsics"]["matrix"] == cam_mm["intrinsics"]["matrix"]


def test_export_routes_write_files_and_zip(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})

    # Extrinsics incomplete -> 422 (no camera configured yet).
    assert client.post("/export", json={"formats": ["caliscope"]}).status_code == 422

    session = manager.current()
    session.cameras.extend(_session().cameras)

    # Nothing is forced (ADR-0026): an empty selection is rejected, and the
    # canonical TOML is only written when 'caliscope' is checked.
    assert client.post("/export", json={"formats": []}).status_code == 422

    response = client.post("/export", json={"formats": ["caliscope", "unity"]})
    assert response.status_code == 200
    names = [f["name"] for f in response.json()["files"]]
    assert names == ["camera_array.toml", "camera_array_unity.json"]  # catalog order
    unity = json.loads((manager.export_dir() / "camera_array_unity.json").read_text())
    assert unity["world_units"] == "mm"
    assert unity["anchor"] == "cam_0"

    archive = client.get("/export/archive")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert sorted(bundle.namelist()) == sorted(names)

    # Units apply to the platform JSONs (the TOMLs keep their mm semantics).
    client.post("/export", json={"formats": ["unity"], "units": "m"})
    unity_m = json.loads((manager.export_dir() / "camera_array_unity.json").read_text())
    assert unity_m["world_units"] == "m"

    assert client.post("/export", json={"formats": ["nope"]}).status_code == 422
    assert manager.current().step.value == "export"  # wizard advanced


def test_export_preview_renders_without_writing(tmp_path: Path) -> None:
    # Dry-run (ADR-0026): the preview returns the exact bytes each target would
    # write, but touches no disk. Content must match what /export then writes.
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})
    manager.current().cameras.extend(_session().cameras)

    preview = client.post("/export/preview", json={"formats": ["caliscope", "unity"], "units": "m"})
    assert preview.status_code == 200
    files = {f["name"]: f for f in preview.json()["files"]}
    assert set(files) == {"camera_array.toml", "camera_array_unity.json"}
    assert files["camera_array.toml"]["language"] == "toml"
    assert files["camera_array_unity.json"]["language"] == "json"
    assert not manager.export_dir().exists()  # nothing written

    client.post("/export", json={"formats": ["unity"], "units": "m"})
    written = (manager.export_dir() / "camera_array_unity.json").read_text()
    assert written == files["camera_array_unity.json"]["content"]


def test_export_config_persists_across_reload(tmp_path: Path) -> None:
    # The export config (units + targets) is session state (ADR-0026): restored
    # on reopen, exposed on the session payload.
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    client.post("/export/config", json={"formats": ["caliscope", "blender"], "units": "m"})
    assert client.get("/session").json()["export_targets"] == ["caliscope", "blender"]

    reopened = SessionManager(tmp_path, "default")
    session = reopened.current()
    assert session.export_units == "m"
    assert session.export_targets == ["caliscope", "blender"]


def test_export_conventions_catalog_route(tmp_path: Path) -> None:
    client = TestClient(create_app(SessionManager(tmp_path, "default")))
    catalog = client.get("/export/conventions").json()["targets"]
    ids = [t["id"] for t in catalog]
    assert ids == ["caliscope", "threejs", "blender", "unity", "unreal"]
