"""Calibration export: Caliscope TOML round-trip + variant math (spec calibration-export)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import rtoml
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.export import (
    WorldFrame,
    aniposelib_document,
    caliscope_document,
    export_targets,
    opencv_document,
    platform_variant,
)
from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
)
from calibration_service.session.manager import SessionManager
from calibration_service.tuning import TUNING

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


# --- Upstream loaders, replayed from their sources -------------------------------
# The promise of ADR-0002/0047 is "loads in the tool", so the tests run each
# tool's own reading logic (transcribed, not imported: none is a dependency).


def _caliscope_v0115_load(text: str) -> dict[int, dict[str, Any]]:
    """Caliscope v0.11.5 ``CameraArray.from_toml`` (cameras/camera_array.py)."""
    data = rtoml.loads(text)
    if not data or "cameras" not in data:
        return {}  # upstream returns CameraArray({}) — silently empty
    cameras: dict[int, dict[str, Any]] = {}
    for cam_id_str, camera in data["cameras"].items():
        rotation = np.asarray(camera.get("rotation"), np.float64)
        if rotation.shape in [(3,), (3, 1)]:
            rotation = cv2.Rodrigues(rotation)[0]
        cameras[int(cam_id_str)] = {
            "size": (camera["size"][0], camera["size"][1]),
            "rotation_count": camera.get("rotation_count", 0),
            "matrix": np.asarray(camera.get("matrix"), np.float64),
            "distortions": np.asarray(camera.get("distortions"), np.float64),
            "rotation": rotation,
            "translation": np.asarray(camera.get("translation"), np.float64),
            "fisheye": camera.get("fisheye", False),
        }
    return cameras


def _caliscope_v054_load(text: str) -> dict[int, dict[str, Any]]:
    """Caliscope v0.5.4 ``Configurator.get_configured_camera_data`` (configurator.py)."""
    cameras: dict[int, dict[str, Any]] = {}
    for key, params in rtoml.loads(text).items():
        if key.startswith("cam_"):
            camera: dict[str, Any] = {
                "size": params["size"],
                "rotation_count": params["rotation_count"],  # KeyError when absent
                "translation": np.asarray(params["translation"]),
                "rotation": cv2.Rodrigues(np.asarray(params["rotation"]))[0],
            }
            if params.get("error") is not None:
                # An `error` makes it read the intrinsics, grid_count without default.
                camera["error"] = params["error"]
                camera["matrix"] = np.asarray(params["matrix"])
                camera["distortions"] = np.asarray(params["distortions"])
                camera["grid_count"] = params["grid_count"]  # KeyError when absent
            cameras[params["port"]] = camera
    return cameras


def _aniposelib_load(text: str) -> list[dict[str, Any]]:
    """aniposelib ``CameraGroup.load`` + ``Camera.load_dict`` (cameras.py)."""
    data = rtoml.loads(text)
    required = ("name", "size", "matrix", "distortions", "rotation", "translation")
    names = [name for name in sorted(data) if name != "metadata"]
    return [{key: data[name][key] for key in required} for name in names]


def test_caliscope_toml_loads_in_caliscope_0_11_5() -> None:
    # It used to load as an EMPTY array: from_toml wants [cameras.<id>] tables.
    cameras = _caliscope_v0115_load(rtoml.dumps(caliscope_document(_session(), SQUARE_MM)))
    assert sorted(cameras) == [0, 1]
    cam_1 = cameras[1]
    assert cam_1["size"] == (640, 480)  # output resolution (resize_factor 0.5)
    assert np.allclose(cam_1["matrix"], K)
    assert np.allclose(cam_1["distortions"], DIST)  # exactly as calibrated
    assert np.allclose(cam_1["rotation"], cv2.Rodrigues(np.array([0.0, np.pi / 2, 0.0]))[0])
    # Caliscope's world unit is the metre: 2 squares x 40 mm = 0.08 m.
    assert np.allclose(cam_1["translation"], [0.08, 0.0, 0.0])
    assert cam_1["rotation_count"] == 0 and cam_1["fisheye"] is False
    assert np.allclose(cameras[0]["rotation"], np.eye(3))  # anchor identity


def test_caliscope_toml_keeps_its_extensions_additive() -> None:
    entry = caliscope_document(_session(), SQUARE_MM)["cameras"]["1"]
    assert entry["name"] == "cam_1"
    assert entry["device_path"] == "/dev/v4l/by-path/cam1"  # id -> physical device
    assert entry["grid_count"] == 400 and entry["error"] == 0.2


def test_aniposelib_toml_loads_in_aniposelib_and_caliscope_0_5_4() -> None:
    text = rtoml.dumps(aniposelib_document(_session(), SQUARE_MM))
    cameras = _aniposelib_load(text)
    assert [c["name"] for c in cameras] == ["cam_0", "cam_1"]
    assert cameras[1]["translation"] == pytest.approx([0.08, 0.0, 0.0])  # metres by default
    # The same tables paste into a Caliscope <= 0.5.4 config.toml: rotation_count
    # (read without default there) is present.
    legacy = _caliscope_v054_load(text)
    assert sorted(legacy) == [0, 1]
    assert legacy[1]["rotation_count"] == 0
    assert np.allclose(legacy[1]["translation"], [0.08, 0.0, 0.0])
    assert legacy[1]["error"] == 0.2 and legacy[1]["grid_count"] == 400
    assert np.allclose(legacy[1]["matrix"], K)


def test_aniposelib_toml_follows_the_export_units() -> None:
    mm = aniposelib_document(_session(), SQUARE_MM, units="mm")
    assert mm["cam_1"]["translation"] == pytest.approx([80.0, 0.0, 0.0])
    assert mm["metadata"] == {"adjusted": False}


def test_export_targets_catalog_lists_both_tomls_plus_platforms() -> None:
    # Backend = single source for the export catalog (ADR-0026): the Caliscope and
    # aniposelib TOMLs (OpenCV axes), the OpenCV contract (ADR-0057) + the four
    # platform JSONs, with display metadata.
    targets = {t.id: t for t in export_targets()}
    assert set(targets) == {
        "caliscope",
        "aniposelib",
        "opencv",
        "threejs",
        "blender",
        "unity",
        "unreal",
    }
    assert targets["opencv"].filename == "camera_array_opencv.json"
    assert targets["caliscope"].filename == "camera_array.toml"
    assert targets["caliscope"].kind == "toml"
    assert targets["aniposelib"].filename == "camera_array_aniposelib.toml"
    assert targets["aniposelib"].kind == "toml"
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
    # The export scales translations by the EXTRINSIC board's measurement, which
    # only exists once that step is walked (ADR-0045).
    client.post("/board", json={"target": "extrinsic", "board": board, "inherited": True})

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
    # No units in the request -> the session preference applies (TUNING default:
    # Caliscope-native metres, ADR-0036).
    assert unity["world_units"] == "m"
    assert unity["anchor"] == "cam_0"

    archive = client.get("/export/archive")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        # The pre-export checks travel with every export (ADR-0057).
        assert sorted(bundle.namelist()) == sorted([*names, "checks.json"])

    # An explicit units override is honoured verbatim (and persists as the new
    # session preference).
    client.post("/export", json={"formats": ["unity"], "units": "mm"})
    unity_mm = json.loads((manager.export_dir() / "camera_array_unity.json").read_text())
    assert unity_mm["world_units"] == "mm"
    # The folder (and so the archive) holds THIS export only: the previous
    # selection's metre TOML must not ship next to the new millimetre JSON.
    with zipfile.ZipFile(io.BytesIO(client.get("/export/archive").content)) as bundle:
        assert sorted(bundle.namelist()) == ["camera_array_unity.json", "checks.json"]

    assert client.post("/export", json={"formats": ["nope"]}).status_code == 422
    assert manager.current().step.value == "export"  # wizard advanced


def test_export_preview_renders_without_writing(tmp_path: Path) -> None:
    # Dry-run (ADR-0026): the preview returns the exact bytes each target would
    # write, but touches no disk. Content must match what /export then writes.
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post("/board", json={"target": "extrinsic", "board": board, "inherited": True})
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
    assert ids == ["caliscope", "aniposelib", "opencv", "threejs", "blender", "unity", "unreal"]


def test_export_refuses_a_camera_without_translation() -> None:
    # Fail loud (ADR-0036): exporting a missing translation as the world ORIGIN
    # produced a plausible file with a teleported camera.
    session = _session()
    session.cameras[1].translation = None
    with pytest.raises(ValueError, match="cam_1"):
        caliscope_document(session, SQUARE_MM)


@pytest.mark.parametrize("writer", ["aniposelib", "threejs"])
def test_unknown_units_are_refused_not_defaulted(writer: str) -> None:
    # Used to fall through to millimetres while the JSON announced the bad unit.
    with pytest.raises(ValueError, match="unknown export units"):
        if writer == "aniposelib":
            aniposelib_document(_session(), SQUARE_MM, units="km")
        else:
            platform_variant(_session(), writer, SQUARE_MM, units="km")


def test_anchor_is_the_lowest_index_camera() -> None:
    # An imported cam_1..N rig has no cam_0: the anchor is the camera the solver
    # holds fixed, the lowest index (ADR-0012) — it used to read null.
    session = _session()
    for camera in session.cameras:
        camera.index += 1
        camera.name = f"cam_{camera.index}"
    assert platform_variant(session, "threejs", SQUARE_MM)["anchor"] == "cam_1"


def test_export_units_have_one_definition() -> None:
    # The unit list exists in TUNING (served to the webapp), the writers' scale
    # table and the API Literal: they must not drift apart.
    from typing import get_args

    from calibration_service.export.camera_array import _UNIT_SCALE
    from calibration_service.transport.api import ExportUnits

    options = set(TUNING.export_units_options)
    assert set(_UNIT_SCALE) == options
    assert set(get_args(ExportUnits)) == options


def test_caliscope_toml_stays_in_metres_whatever_the_units(tmp_path: Path) -> None:
    # A mm translation in a Caliscope file would read 1000x too large there
    # (ADR-0047): the units knob scales every target but this one.
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post("/board", json={"target": "extrinsic", "board": board, "inherited": True})
    manager.current().cameras.extend(_session().cameras)

    files = client.post(
        "/export/preview", json={"formats": ["caliscope", "aniposelib", "threejs"], "units": "mm"}
    ).json()["files"]
    content = {f["name"]: f["content"] for f in files}
    caliscope = _caliscope_v0115_load(content["camera_array.toml"])
    assert np.allclose(caliscope[1]["translation"], [0.08, 0.0, 0.0])  # metres
    aniposelib = rtoml.loads(content["camera_array_aniposelib.toml"])
    assert aniposelib["cam_1"]["translation"] == pytest.approx([80.0, 0.0, 0.0])  # mm
    assert json.loads(content["camera_array_threejs.json"])["world_units"] == "mm"


def test_the_opencv_contract_is_the_threejs_view_in_metres() -> None:
    # ADR-0057: R and t are the three.js variant's view block (the same basis M,
    # world -> camera, Y-up right-handed), t always in metres and a 3x1 column;
    # cameras sorted by port, the two errors apart, an import's path dropped.
    session = _session()
    session.cameras.reverse()
    session.cameras[0].device_path = "import:cam_1.mp4"
    world = WorldFrame("target", "centre of the marker of group 3", "y", 3)
    document = opencv_document(session, 40.0, world, exported_at="2026-10-10T00:00:00+00:00")
    threejs = platform_variant(session, "threejs", 40.0, units="m")
    view = {c["name"]: c["view"] for c in threejs["cameras"]}
    assert document["format"] == "realtime-calib/opencv-cameras"
    assert document["version"] == 1
    assert [c["port"] for c in document["cameras"]] == [0, 1]
    for camera in document["cameras"]:
        assert np.allclose(camera["R"], view[camera["name"]]["R"])
        assert np.asarray(camera["t"]).shape == (3, 1)
        assert np.allclose(np.asarray(camera["t"]).ravel(), view[camera["name"]]["t"])
        assert camera["intrinsic_error_px"] == 0.2
        assert camera["extrinsic_error_px"] == 0.3
        assert camera["resolution"] == [640, 480]
    assert document["cameras"][1]["t"] == [[0.08], [0.0], [0.0]]  # 2 squares of 40 mm
    assert document["cameras"][0]["device_path"] == "/dev/v4l/by-path/cam0"
    assert document["cameras"][1]["device_path"] is None  # import: a file, not a device
    assert document["world"] == {
        "frame": "target",
        "origin": "centre of the marker of group 3",
        "group": 3,
        "up": "y",
    }
    unframed = WorldFrame("anchor_camera", "optical centre of cam_0", None)
    world_block = opencv_document(session, 40.0, unframed, exported_at="x")["world"]
    assert "up" not in world_block  # not asserted until the world is framed


def test_the_opencv_target_ignores_the_export_units(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post("/board", json={"target": "extrinsic", "board": board, "inherited": True})
    manager.current().cameras.extend(_session().cameras)
    exports = []
    for units in ("mm", "m"):
        response = client.post("/export", json={"formats": ["opencv"], "units": units})
        assert response.status_code == 200
        exports.append(json.loads((manager.export_dir() / "camera_array_opencv.json").read_text()))
    assert exports[0]["cameras"] == exports[1]["cameras"]
    checks = json.loads((manager.export_dir() / "checks.json").read_text())["checks"]
    assert [c["id"] for c in checks] == [
        "camera_error",
        "epipolar",
        "target_rigidity",
        "frame",
        "cameras_above_floor",
        "reference",
    ]
    assert checks[3]["status"] == "warn"  # no solve on disk: the world is unknown
    served = client.get("/export/checks").json()["checks"]
    assert [c["status"] for c in served] == [c["status"] for c in checks]


def test_the_opencv_errors_never_fall_back_and_the_world_reports_its_drift() -> None:
    session = _session()
    session.cameras[1].extrinsic_error = None
    world = WorldFrame(
        "target", "centre of the marker of group 3, as framed", "y", 3, 0.1, (), 0.0023
    )
    document = opencv_document(session, 40.0, world, exported_at="x")
    assert document["cameras"][1]["extrinsic_error_px"] is None  # not the intrinsic one
    assert document["cameras"][1]["intrinsic_error_px"] == 0.2
    assert document["world"]["target_offset_m"] == 0.0023


def test_a_malformed_ba_inputs_does_not_block_the_export(tmp_path: Path) -> None:
    # A truncated ba_inputs.json made POST /export and GET /export/checks fail with a 500.
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post("/board", json={"target": "extrinsic", "board": board, "inherited": True})
    manager.current().cameras.extend(_session().cameras)
    directory = manager.extrinsic_dir()
    directory.mkdir(parents=True, exist_ok=True)
    result = {
        "cameras": ["cam_0", "cam_1"],
        "rotations": {"cam_0": [0.0, 0.0, 0.0], "cam_1": [0.0, float(np.pi / 2), 0.0]},
        "translations": {"cam_0": [0.0, 0.0, 0.0], "cam_1": [2.0, 0.0, 0.0]},
        "per_camera_error": {"cam_0": 0.3, "cam_1": 0.3},
        "error": 0.3,
        "pair_errors": {},
        "group_count": 1,
        "point_count": 1,
    }
    (directory / "result.json").write_text(json.dumps(result))
    truncated = {
        "obs_camera": [0, 1],
        "obs_point": [0, 0],
        "obs_norm": [[0.0, 0.0]],
        "obs_px": [[0.0, 0.0], [0.0, 0.0]],
        "point_corner": [0],
    }
    (directory / "ba_inputs.json").write_text(json.dumps(truncated))
    response = client.post("/export", json={"formats": ["opencv"], "units": "m"})
    assert response.status_code == 200
    checks = {
        c["id"]: c for c in json.loads((manager.export_dir() / "checks.json").read_text())["checks"]
    }
    assert checks["epipolar"]["status"] == "unavailable"
    assert checks["epipolar"]["detail"].startswith("could not run")
    assert client.get("/export/checks").status_code == 200
