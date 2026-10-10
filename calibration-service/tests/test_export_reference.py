"""Re-alignment on a reference calibration (ADR-0061)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from numpy.typing import NDArray

from calibration_service.app import create_app
from calibration_service.calibration import ExtrinsicResult, camera_centres
from calibration_service.calibration.extrinsic import reorient_result
from calibration_service.export import (
    WorldFrame,
    align,
    opencv_document,
    parse_reference,
    world_frame,
)
from calibration_service.export.opencv import BASIS
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.session import CalibrationSession, CameraConfig, CameraStatus
from calibration_service.session.manager import SessionManager

MARKER = CalibrationBoard(
    board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=2, rows=2, marker_id=8
)
UNIT_M = MARKER.marker_size_mm / 1000.0
# Four cameras around a level floor marker, in the solver's world (y down, marker
# units): 3 m out, 2.1 m up.
CENTRES = {
    "cam_0": [100.0, -70.0, 0.0],
    "cam_1": [0.0, -72.0, 100.0],
    "cam_2": [-100.0, -69.0, 5.0],
    "cam_3": [3.0, -71.0, -100.0],
}
# A floor marker framed at the origin: corners y up on the board, so its corner-order
# normal is the solver's up (-y).
QUAD = [[-0.5, 0.0, -0.5], [0.5, 0.0, -0.5], [0.5, 0.0, 0.5], [-0.5, 0.0, 0.5]]


def _pose(centre: list[float]) -> tuple[list[float], list[float]]:
    # Cameras looking down at the origin: any rotation does, only centres serve.
    rotation = cv2.Rodrigues(np.array([0.4, 0.2, -0.1]))[0]
    return [float(v) for v in cv2.Rodrigues(rotation)[0].ravel()], [
        float(v) for v in -rotation @ np.asarray(centre)
    ]


def _result(centres: dict[str, list[float]] = CENTRES) -> ExtrinsicResult:
    poses = {n: _pose(c) for n, c in centres.items()}
    return ExtrinsicResult(
        cameras=list(centres),
        rotations={n: p[0] for n, p in poses.items()},
        translations={n: p[1] for n, p in poses.items()},
        per_camera_error={n: 0.2 for n in centres},
        error=0.2,
        pair_errors={},
        group_count=1,
        point_count=4,
        framed_group=0,
        board_quads=[QUAD],
    )


def _session(result: ExtrinsicResult, imported: bool = False) -> CalibrationSession:
    cameras = []
    for index, name in enumerate(result.cameras):
        cameras.append(
            CameraConfig(
                index=index,
                name=name,
                prefix="cam",
                device_path=f"import:{name}.mp4" if imported else f"/dev/v4l/by-path/usb-{index}",
                device_node=f"/dev/video{index}",
                width=1920,
                height=1080,
                resize_factor=0.5,
                fps=30,
                status=CameraStatus.EXTRINSIC_DONE,
                matrix=[[800.0, 0.0, 480.0], [0.0, 800.0, 270.0], [0.0, 0.0, 1.0]],
                distortions=[0.0] * 5,
                calibration_error=0.2,
                grid_count=40,
                rotation=result.rotations[name],
                translation=result.translations[name],
                extrinsic_error=0.2,
            )
        )
    return CalibrationSession(session_id="room", cameras=cameras)


def _world(result: ExtrinsicResult) -> WorldFrame:
    return world_frame(result, MARKER, "cam_0")


def _reference(
    result: ExtrinsicResult, change: tuple[NDArray[np.float64], NDArray[np.float64]], **kw: Any
) -> dict[str, Any]:
    """This solve's opencv export, its world moved by ``change`` (export frame, metres)."""
    rotation, shift = change
    transform = np.eye(4)
    transform[:3, :3] = BASIS.T @ rotation @ BASIS
    transform[:3, 3] = BASIS.T @ shift / UNIT_M
    moved = reorient_result(result, transform)
    world = WorldFrame("target", "x", "y", 0)
    document = opencv_document(_session(moved, **kw), MARKER.marker_size_mm, world, exported_at="t")
    return document


def _export_centres(result: ExtrinsicResult) -> NDArray[np.float64]:
    return np.stack([BASIS @ c * UNIT_M for c in camera_centres(result).values()])


def _rot(axis: str, degrees: float) -> NDArray[np.float64]:
    vector = np.zeros(3)
    vector["xyz".index(axis)] = np.radians(degrees)
    return np.asarray(cv2.Rodrigues(vector)[0], np.float64)


def test_an_exported_calibration_is_its_own_reference() -> None:
    # The round trip catches a doubled or forgotten basis and a unit slip.
    result = _result()
    reference = parse_reference(_reference(result, (np.eye(3), np.zeros(3))), "self.json")
    assert reference.format == "opencv-v1"
    for mode in ("floor", "rigid"):
        report = align(result, _session(result), MARKER, reference, _world(result), mode).report
        assert report.refused is None
        assert report.matched_by == "device_path"
        assert report.rotation_deg == pytest.approx(0.0, abs=1e-6)
        assert report.residual_rms_m == pytest.approx(0.0, abs=1e-9)
        assert report.scale_ratio == pytest.approx(1.0)


def test_floor_mode_recovers_a_yaw_and_keeps_the_floor() -> None:
    result = _result()
    change = (_rot("y", 7.0), np.array([0.3, 0.05, -0.2]))
    reference = parse_reference(_reference(result, change), "room.json")
    fit = align(result, _session(result), MARKER, reference, _world(result), "floor")
    report = fit.report
    assert report.refused is None and fit.transform is not None
    assert report.rotation_deg == pytest.approx(7.0)
    assert report.translation_m == pytest.approx([0.3, 0.0, -0.2])  # vertical not applied
    assert report.vertical_offset_m == pytest.approx(0.05)
    assert report.residual_rms_m == pytest.approx(0.05)  # the unapplied vertical offset
    moved = reorient_result(result, fit.transform)
    expected = _export_centres(result) @ change[0].T + [0.3, 0.0, -0.2]
    assert np.allclose(_export_centres(moved), expected, atol=1e-9)
    # The floor target stays level, in its plane: the world is still framed on it.
    assert _world(replace(moved, alignment={"reference": "room.json", "mode": "floor"})).up == "y"


def test_floor_mode_reports_the_tilt_it_does_not_apply() -> None:
    result = _result()
    change = (_rot("x", 2.0) @ _rot("y", -4.0), np.zeros(3))
    reference = parse_reference(_reference(result, change), "room.json")
    floor = align(result, _session(result), MARKER, reference, _world(result), "floor").report
    rigid = align(result, _session(result), MARKER, reference, _world(result), "rigid").report
    assert floor.tilt_deg == pytest.approx(2.0, abs=0.05)
    assert floor.rotation_deg == pytest.approx(4.0, abs=0.2)
    assert rigid.rotation_deg == pytest.approx(np.degrees(np.arccos((np.trace(change[0]) - 1) / 2)))
    assert rigid.residual_rms_m == pytest.approx(0.0, abs=1e-9)
    assert rigid.tilt_deg == pytest.approx(2.0, abs=0.05)


def test_a_port_permutation_is_refused_and_device_paths_defeat_it() -> None:
    # A new session took the by-path order: ports 0..3 are the reference's 2, 3, 0, 1.
    result = _result()
    document = _reference(result, (np.eye(3), np.zeros(3)))
    permuted = [2, 3, 0, 1]
    for camera in document["cameras"]:
        camera["port"] = permuted[camera["port"]]
    bare = {
        "cameras": [
            {k: c[k] for k in ("port", "K", "distCoef", "R", "t")} for c in document["cameras"]
        ]
    }
    by_port = parse_reference(bare, "bare.json")
    assert by_port.format == "bare"
    report = align(result, _session(result), MARKER, by_port, _world(result), "floor").report
    assert report.matched_by == "port"
    assert report.refused is not None and "implausible" in report.refused
    assert report.rotation_deg == pytest.approx(180.0, abs=1.0)
    with_devices = parse_reference(document, "room.json")
    report = align(result, _session(result), MARKER, with_devices, _world(result), "floor").report
    assert report.refused is None and report.matched_by == "device_path"
    assert report.rotation_deg == pytest.approx(0.0, abs=1e-6)


def test_an_imported_session_matches_by_port() -> None:
    result = _result()
    reference = parse_reference(_reference(result, (np.eye(3), np.zeros(3))), "room.json")
    session = _session(result, imported=True)
    report = align(result, session, MARKER, reference, _world(result), "rigid").report
    assert report.matched_by == "port" and report.refused is None


def test_refusals() -> None:
    result = _result()
    session = _session(result)
    world = _world(result)
    # Units: a reference in millimetres spreads 1000 times wider.
    document = _reference(result, (np.eye(3), np.zeros(3)))
    for camera in document["cameras"]:
        camera["t"] = [[1000.0 * v[0]] for v in camera["t"]]
    report = align(
        result, session, MARKER, parse_reference(document, "mm.json"), world, "rigid"
    ).report
    assert report.refused is not None and "units" in report.refused
    # Floor mode needs a framed, level floor.
    reference = parse_reference(_reference(result, (np.eye(3), np.zeros(3))), "room.json")
    unframed = WorldFrame("anchor_camera", "optical centre of cam_0", None)
    report = align(result, session, MARKER, reference, unframed, "floor").report
    assert report.refused is not None and "frame" in report.refused
    # Rigid mode needs three cameras off one line.
    line = {n: [10.0 * i, -70.0, 0.0] for i, n in enumerate(CENTRES)}
    on_a_line = _result(line)
    reference = parse_reference(_reference(on_a_line, (np.eye(3), np.zeros(3))), "line.json")
    report = align(on_a_line, _session(on_a_line), MARKER, reference, world, "rigid").report
    assert report.refused is not None and "line" in report.refused
    # Another room: the same layout, cameras 30 cm elsewhere.
    document = _reference(result, (np.eye(3), np.zeros(3)))
    for camera, shift in zip(
        document["cameras"], ([0.3, 0, 0], [0, 0, -0.3], [-0.3, 0, 0], [0, 0, 0.3]), strict=True
    ):
        rotation = np.asarray(camera["R"])
        centre = -rotation.T @ np.asarray(camera["t"]).ravel() + shift
        camera["t"] = [[v] for v in -rotation @ centre]
    report = align(
        result, session, MARKER, parse_reference(document, "other.json"), world, "floor"
    ).report
    assert report.refused is not None and "does not match" in report.refused
    assert report.residual_rms_m == pytest.approx(0.3, rel=0.01)
    # No device of the reference on this rig.
    document = _reference(result, (np.eye(3), np.zeros(3)))
    for camera in document["cameras"]:
        camera["device_path"] = "/dev/v4l/by-path/elsewhere-" + str(camera["port"])
    report = align(
        result, session, MARKER, parse_reference(document, "x.json"), world, "floor"
    ).report
    assert report.refused is not None and "device paths" in report.refused


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "JSON object"),
        ({"format": "caliscope", "cameras": []}, "unsupported"),
        ({"format": "realtime-calib/opencv-cameras", "version": 2, "cameras": []}, "unsupported"),
        ({"cameras": [{"port": 0, "t": [0, 0, 0]}]}, "port 0: R is missing"),
        ({"cameras": [{"port": 0, "R": np.eye(3).ravel().tolist(), "t": [0, 0, 0]}]}, "3x3"),
        ({"cameras": [{"port": 0, "R": {"a": 1}, "t": [0, 0, 0]}]}, "R must hold numbers"),
        ({"cameras": [{"port": 0, "R": np.eye(3).tolist(), "t": {"x": 1}}]}, "t must hold numbers"),
        (
            {"cameras": [{"port": 0, "R": [[1, 0, 0], [0, 1, 0], [0, 0, "a"]], "t": [0, 0, 0]}]},
            "numbers",
        ),
        ({"cameras": [{"port": 0, "R": (2 * np.eye(3)).tolist(), "t": [0, 0, 0]}]}, "proper"),
        ({"cameras": [{"port": True, "R": np.eye(3).tolist(), "t": [0, 0, 0]}]}, "port"),
        (
            {
                "cameras": [
                    {"port": p, "device_path": "/dev/a", "R": np.eye(3).tolist(), "t": [0, 0, p]}
                    for p in (0, 1)
                ]
            },
            "duplicate device paths",
        ),
        ({"cameras": [{"port": 0, "R": np.diag([1, 1, -1]).tolist(), "t": [0, 0, 0]}]}, "proper"),
        ({"cameras": [{"port": 0, "R": np.eye(3).tolist(), "t": [0, 0]}]}, "3 values"),
        ({"cameras": [{"R": np.eye(3).tolist(), "t": [0, 0, 0]}]}, "port"),
        ({"cameras": [{"port": 0, "R": np.eye(3).tolist(), "t": [0, 0, 0]}] * 2}, "duplicate"),
        ({"cameras": [{"port": 0, "R": np.eye(3).tolist(), "t": [0, 0, 0]}]}, "at least 2"),
    ],
)
def test_unsupported_references_are_refused(document: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_reference(document, "x.json")


def _client(tmp_path: Path) -> tuple[SessionManager, TestClient, ExtrinsicResult]:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "aruco", "dictionary": "DICT_4X4_100"}
    assert client.post("/board", json={"target": "extrinsic", "board": board}).status_code == 200
    result = _result()
    manager.current().cameras.extend(_session(result).cameras)
    directory = manager.extrinsic_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(json.dumps(result.__dict__))
    return manager, client, result


def test_the_reference_routes_preview_apply_and_report(tmp_path: Path) -> None:
    manager, client, result = _client(tmp_path)
    assert client.get("/extrinsic/reference").status_code == 404
    change = (_rot("y", 5.0), np.array([0.2, 0.0, 0.1]))
    body = {"name": "room.json", "document": _reference(result, change)}
    deposited = client.put("/extrinsic/reference", json=body)
    assert deposited.status_code == 200
    state = deposited.json()
    assert state["applied"] is None
    assert state["preview"]["floor"]["rotation_deg"] == pytest.approx(5.0)
    assert state["preview"]["rigid"]["refused"] is None
    checks = {c["id"]: c for c in client.get("/export/checks").json()["checks"]}
    assert checks["reference"]["status"] == "warn"  # loaded, not applied

    aligned = client.post("/extrinsic/orient", json={"op": "align", "mode": "floor"})
    assert aligned.status_code == 200
    alignment = aligned.json()["alignment"]
    assert alignment["reference"] == "room.json" and alignment["mode"] == "floor"
    checks = {c["id"]: c for c in client.get("/export/checks").json()["checks"]}
    assert checks["reference"]["status"] == "ok"
    assert checks["frame"]["status"] == "ok" and "reference room.json" in checks["frame"]["detail"]
    after = client.get("/extrinsic/reference").json()
    assert after["applied"]["reference"] == "room.json"
    assert after["preview"]["floor"]["rotation_deg"] == pytest.approx(0.0, abs=1e-6)

    exported = client.post("/export", json={"formats": ["opencv"], "units": "m"})
    assert exported.status_code == 200
    document = json.loads((manager.export_dir() / "camera_array_opencv.json").read_text())
    assert document["world"]["frame"] == "reference"
    assert document["world"]["up"] == "y"  # the floor target still level
    assert document["world"]["alignment"]["mode"] == "floor"
    assert document["world"]["origin"].endswith("on the floor of the target of group 0")
    assert "target_offset_m" not in document["world"]

    # A rotation moves the world another way: the re-alignment no longer holds.
    rotated = client.post("/extrinsic/orient", json={"op": "rotate", "axis": "y", "degrees": 90})
    assert rotated.json()["alignment"] is None
    assert client.delete("/extrinsic/reference").json() == {"deleted": True}
    assert client.get("/extrinsic/reference").status_code == 404


def test_a_refused_alignment_leaves_the_world_alone(tmp_path: Path) -> None:
    manager, client, result = _client(tmp_path)
    change = (_rot("y", 40.0), np.zeros(3))
    client.put(
        "/extrinsic/reference", json={"name": "far.json", "document": _reference(result, change)}
    )
    response = client.post("/extrinsic/orient", json={"op": "align", "mode": "floor"})
    assert response.status_code == 422 and "implausible" in response.json()["detail"]
    stored = json.loads((manager.extrinsic_dir() / "result.json").read_text())
    assert stored["rotations"] == result.rotations
    bad = client.put("/extrinsic/reference", json={"name": "x.toml", "document": {"cameras": "?"}})
    assert bad.status_code == 422


def test_minimize_keeps_the_alignment_and_a_recompute_keeps_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from calibration_service.transport import api

    manager, client, result = _client(tmp_path)
    client.put(
        "/extrinsic/reference",
        json={"name": "room.json", "document": _reference(result, (_rot("y", 3.0), np.zeros(3)))},
    )
    aligned = ExtrinsicResult(**client.post("/extrinsic/orient", json={"op": "align"}).json())
    (manager.extrinsic_dir() / "ba_inputs.json").write_text(
        json.dumps(
            {"obs_camera": [], "obs_point": [], "obs_norm": [], "obs_px": [], "point_corner": []}
        )
    )
    # refine_result rebuilds the result field by field: the route restores the alignment.
    monkeypatch.setattr(api, "refine_result", lambda r, *_: replace(r, alignment=None))
    refined = client.post("/extrinsic/minimize")
    assert refined.status_code == 200
    assert refined.json()["alignment"] == aligned.alignment
    # A fresh solve discards the result files, not the reference: it can be re-applied.
    manager._discard_extrinsic_files()
    assert (manager.extrinsic_dir() / "reference.json").is_file()


def test_rigid_mode_aligns_an_unframed_world(tmp_path: Path) -> None:
    # The use ADR-0061 gives the rigid mode: a world never framed on a target.
    manager, client, result = _client(tmp_path)
    unframed = replace(result, framed_group=None, board_quads=[])
    (manager.extrinsic_dir() / "result.json").write_text(json.dumps(unframed.__dict__))
    change = (_rot("x", 3.0) @ _rot("y", 6.0), np.array([0.1, -0.05, 0.2]))
    body = {"name": "room.json", "document": _reference(unframed, change)}
    assert client.put("/extrinsic/reference", json=body).status_code == 200
    before = (manager.extrinsic_dir() / "result.json").read_text()
    floor = client.post("/extrinsic/orient", json={"op": "align", "mode": "floor"})
    assert floor.status_code == 422 and "frame" in floor.json()["detail"]
    assert (manager.extrinsic_dir() / "result.json").read_text() == before
    rigid = client.post("/extrinsic/orient", json={"op": "align", "mode": "rigid"})
    assert rigid.status_code == 200
    checks = {c["id"]: c for c in client.get("/export/checks").json()["checks"]}
    assert (
        checks["frame"]["status"] == "ok" and "no target verifies it" in checks["frame"]["detail"]
    )
    assert checks["cameras_above_floor"]["status"] == "unavailable"
    assert checks["reference"]["status"] == "ok"
    client.post("/export", json={"formats": ["opencv"], "units": "m"})
    world = json.loads((manager.export_dir() / "camera_array_opencv.json").read_text())["world"]
    assert world["frame"] == "reference" and "up" not in world and "target_offset_m" not in world
    assert world["alignment"]["mode"] == "rigid"


def test_floor_mode_needs_only_two_cameras() -> None:
    two = {n: CENTRES[n] for n in ("cam_0", "cam_1")}
    result = _result(two)
    reference = parse_reference(_reference(result, (_rot("y", 4.0), np.zeros(3))), "two.json")
    floor = align(result, _session(result), MARKER, reference, _world(result), "floor").report
    assert floor.refused is None and floor.rotation_deg == pytest.approx(4.0)
    assert floor.tilt_deg is None  # two centres cannot tell a tilt
    rigid = align(result, _session(result), MARKER, reference, _world(result), "rigid").report
    assert rigid.refused is not None and "3 matched" in rigid.refused


def test_scale_bounds_and_the_angle_uncertainty() -> None:
    result = _result()
    world = _world(result)
    for factor, refused in ((1.2, False), (1.3, True)):
        document = _reference(result, (np.eye(3), np.zeros(3)))
        for camera in document["cameras"]:
            camera["t"] = [[factor * v[0]] for v in camera["t"]]
        report = align(
            result, _session(result), MARKER, parse_reference(document, "s.json"), world, "rigid"
        ).report
        assert report.scale_ratio == pytest.approx(factor)
        assert (report.refused is not None and "units" in report.refused) is refused
    # The angle uncertainty: the residual RMS over the centres' second spread.
    document = _reference(result, (np.eye(3), np.zeros(3)))
    document["cameras"][0]["t"] = [
        [v[0] + d] for v, d in zip(document["cameras"][0]["t"], (0.03, 0, 0), strict=True)
    ]
    report = align(
        result, _session(result), MARKER, parse_reference(document, "n.json"), world, "rigid"
    ).report
    centred = _export_centres(result) - _export_centres(result).mean(axis=0)
    second = np.linalg.svd(centred, compute_uv=False)[1]
    assert report.angle_sigma_deg == pytest.approx(np.degrees(report.residual_rms_m / second))


def test_a_mirrored_reference_is_fit_by_a_proper_rotation() -> None:
    # Centres mirrored through x: no proper rotation maps them, the fit stays one.
    result = _result()
    document = _reference(result, (np.eye(3), np.zeros(3)))
    for camera in document["cameras"]:
        rotation = np.asarray(camera["R"])
        centre = -rotation.T @ np.asarray(camera["t"]).ravel()
        centre[0] = -centre[0]
        camera["t"] = [[v] for v in -rotation @ centre]
    fit = align(
        result,
        _session(result),
        MARKER,
        parse_reference(document, "m.json"),
        _world(result),
        "rigid",
    )
    assert fit.report.refused is not None
    assert fit.transform is None
    # A reflection would fit them exactly; the proper rotation leaves a residual.
    assert fit.report.residual_rms_m is not None and fit.report.residual_rms_m > 0.01


def test_the_reference_check_reads_a_rigid_alignment_and_a_refusal(tmp_path: Path) -> None:
    manager, client, result = _client(tmp_path)
    client.put(
        "/extrinsic/reference",
        json={"name": "far.json", "document": _reference(result, (_rot("y", 40.0), np.zeros(3)))},
    )
    check = {c["id"]: c for c in client.get("/export/checks").json()["checks"]}["reference"]
    assert check["status"] == "warn" and "implausible" in check["detail"]
    (manager.extrinsic_dir() / "reference.json").write_text("{not json")
    assert client.get("/extrinsic/reference").status_code == 422
    check = {c["id"]: c for c in client.get("/export/checks").json()["checks"]}["reference"]
    assert check["status"] == "warn" and "unreadable" in check["detail"]
    malformed = {
        "cameras": [
            {"port": 0, "R": {"a": 1}, "t": [0, 0, 0]},
            {"port": 1, "R": np.eye(3).tolist(), "t": [0, 0, 1]},
        ]
    }
    refused = client.put("/extrinsic/reference", json={"name": "bad.json", "document": malformed})
    assert refused.status_code == 422 and "R must hold numbers" in refused.json()["detail"]
