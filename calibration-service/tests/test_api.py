"""Tests for the HTTP API: session, camera detection (mocked), configuration."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.models.camera import CameraDevice, CameraMode, Resolution
from calibration_service.recording import VideoRecorder, preview_path
from calibration_service.session.manager import SessionManager
from calibration_service.session.store import load_session


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(SessionManager(tmp_path, "default")))


def test_get_session_returns_fresh_session(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/session")

    assert response.status_code == 200
    body = response.json()
    assert body["step"] == "intrinsic_board"
    assert body["mode"] == "new-realtime"
    assert body["cameras"] == []


def test_no_active_session_returns_404_and_locks_mutations(tmp_path: Path) -> None:
    # Fresh service: no active session (ADR-0028). GET /session -> 404 (webapp maps
    # it to a null session, rail locked); a session-scoped mutation -> uniform 409.
    client = TestClient(create_app(SessionManager(tmp_path)))
    assert client.get("/session").status_code == 404
    assert client.post("/cameras/config", json={"prefix": "cam", "cameras": []}).status_code == 409


def test_create_session_activates_a_fresh_folder(tmp_path: Path) -> None:
    client = TestClient(create_app(SessionManager(tmp_path)))
    response = client.post("/sessions", json={"session_id": "manip-01"})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "manip-01"
    assert body["step"] == "intrinsic_board"  # from scratch
    assert body["cameras"] == []
    # Now active: GET /session returns it, and it is persisted on disk.
    assert client.get("/session").json()["session_id"] == "manip-01"
    assert load_session(tmp_path, "manip-01").session_id == "manip-01"


def test_create_session_rejects_duplicate_and_invalid_names(tmp_path: Path) -> None:
    client = TestClient(create_app(SessionManager(tmp_path)))
    assert client.post("/sessions", json={"session_id": "dup"}).status_code == 200
    assert client.post("/sessions", json={"session_id": "dup"}).status_code == 409
    for bad in ["", ".", "..", "a/b", "../evil", "space bad", ".hidden"]:
        assert client.post("/sessions", json={"session_id": bad}).status_code == 422, bad


def test_open_session_switches_the_active_one(tmp_path: Path) -> None:
    client = TestClient(create_app(SessionManager(tmp_path)))
    client.post("/sessions", json={"session_id": "alpha"})
    client.post("/sessions", json={"session_id": "beta"})  # active becomes beta
    assert client.get("/session").json()["session_id"] == "beta"

    assert client.post("/sessions/open", json={"session_id": "alpha"}).status_code == 200
    assert client.get("/session").json()["session_id"] == "alpha"
    assert client.post("/sessions/open", json={"session_id": "ghost"}).status_code == 404


def test_sessions_location_returns_the_root(tmp_path: Path) -> None:
    client = TestClient(create_app(SessionManager(tmp_path)))
    assert client.get("/sessions/location").json() == {"root": tmp_path.name}


def test_detect_returns_cameras_with_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = [
        CameraDevice(
            index=0,
            device_path="/dev/v4l/by-path/cam-a",
            device_node="/dev/video0",
            modes=(CameraMode("MJPG", Resolution(1920, 1080), (30.0,)),),
        )
    ]
    monkeypatch.setattr(
        "calibration_service.session.manager.enumerate_camera_devices", lambda: fake
    )

    response = _client(tmp_path).post("/cameras/detect")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["device_node"] == "/dev/video0"
    assert body[0]["modes"][0] == {
        "pixel_format": "MJPG",
        "width": 1920,
        "height": 1080,
        "fps": [30.0],
    }


def test_configure_cameras_persists_and_advances(tmp_path: Path) -> None:
    payload = {
        "prefix": "cam",
        "cameras": [
            {
                "index": 0,
                "device_path": "/dev/v4l/by-path/cam-a",
                "device_node": "/dev/video0",
                "width": 1920,
                "height": 1080,
                "resize_factor": 0.3333,
                "fps": 30,
            },
            {
                "index": 1,
                "device_path": "/dev/v4l/by-path/cam-b",
                "device_node": "/dev/video2",
                "width": 1280,
                "height": 720,
                "resize_factor": 1.0,
                "fps": 30,
            },
        ],
    }

    response = _client(tmp_path).post("/cameras/config", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["step"] == "intrinsic_capture"
    assert [c["name"] for c in body["cameras"]] == ["cam_0", "cam_1"]

    # Persisted to disk: a fresh load reflects the config.
    reloaded = load_session(tmp_path, "default")
    assert len(reloaded.cameras) == 2
    assert reloaded.cameras[0].name == "cam_0"
    assert reloaded.cameras[0].status.value == "configured"


def test_reorder_cameras_persists_and_keeps_calibrations(tmp_path: Path) -> None:
    # Drag-reorder persistence: /cameras/order permutes index + position-based
    # name WITHOUT rebuilding the configs — calibrations follow the device.
    client = _client(tmp_path)
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [
                {
                    "index": 0,
                    "device_path": "/dev/v4l/by-path/cam-a",
                    "device_node": "/dev/video0",
                    "width": 1280,
                    "height": 720,
                    "resize_factor": 1.0,
                    "fps": 30,
                },
                {
                    "index": 1,
                    "device_path": "/dev/v4l/by-path/cam-b",
                    "device_node": "/dev/video2",
                    "width": 1280,
                    "height": 720,
                    "resize_factor": 1.0,
                    "fps": 30,
                },
            ],
        },
    )
    # Give cam-a a calibration to prove it SURVIVES the reorder.
    matrix = [[800.0, 0.0, 640.0], [0.0, 800.0, 360.0], [0.0, 0.0, 1.0]]
    manager = SessionManager(tmp_path, "default")
    manager.current().cameras[0].matrix = matrix
    reordered = manager.reorder_cameras(
        ["/dev/v4l/by-path/cam-b", "/dev/v4l/by-path/cam-a"]
    )
    assert [c.device_path for c in reordered.cameras] == [
        "/dev/v4l/by-path/cam-b",
        "/dev/v4l/by-path/cam-a",
    ]
    assert [c.name for c in reordered.cameras] == ["cam_0", "cam_1"]
    # The calibration moved WITH its device (cam-a is now index 1 / cam_1).
    assert reordered.cameras[1].matrix == matrix

    # Persisted: a fresh load sees the new order and the kept calibration.
    reloaded = load_session(tmp_path, "default")
    assert [c.device_path for c in reloaded.cameras] == [
        "/dev/v4l/by-path/cam-b",
        "/dev/v4l/by-path/cam-a",
    ]
    assert reloaded.cameras[1].matrix == matrix

    # Route-level guard: unknown paths -> 422.
    response = _client(tmp_path).post(
        "/cameras/order", json={"device_paths": ["/dev/v4l/by-path/nope"]}
    )
    assert response.status_code == 422


def test_validate_intrinsic_guards_then_advances_to_extrinsic(tmp_path: Path) -> None:
    # Operator sign-off: refused while any camera lacks intrinsics, then advances
    # the persisted wizard step to 'extrinsic_capture' (the webapp rail follows it).
    from calibration_service.models.session import CameraStatus

    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [
                {
                    "index": 0,
                    "device_path": "/dev/v4l/by-path/cam-a",
                    "device_node": "/dev/video0",
                    "width": 1280,
                    "height": 720,
                    "resize_factor": 1.0,
                    "fps": 30,
                },
                {
                    "index": 1,
                    "device_path": "/dev/v4l/by-path/cam-b",
                    "device_node": "/dev/video2",
                    "width": 1280,
                    "height": 720,
                    "resize_factor": 1.0,
                    "fps": 30,
                },
            ],
        },
    )

    assert client.post("/intrinsic/validate").status_code == 422

    for camera in manager.current().cameras:
        camera.status = CameraStatus.INTRINSIC_DONE

    response = client.post("/intrinsic/validate")
    assert response.status_code == 200
    assert response.json()["step"] == "extrinsic_capture"
    assert load_session(tmp_path, "default").step.value == "extrinsic_capture"


def test_validate_extrinsic_guards_then_advances_to_export(tmp_path: Path) -> None:
    # Operator sign-off: refused while any camera lacks a pose, then advances
    # the persisted wizard step to 'export' (the webapp rail follows it).
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [
                {
                    "index": 0,
                    "device_path": "/dev/v4l/by-path/cam-a",
                    "device_node": "/dev/video0",
                    "width": 1280,
                    "height": 720,
                    "resize_factor": 1.0,
                    "fps": 30,
                },
                {
                    "index": 1,
                    "device_path": "/dev/v4l/by-path/cam-b",
                    "device_node": "/dev/video2",
                    "width": 1280,
                    "height": 720,
                    "resize_factor": 1.0,
                    "fps": 30,
                },
            ],
        },
    )

    assert client.post("/extrinsic/validate").status_code == 422

    for camera in manager.current().cameras:
        camera.rotation = [0.0, 0.0, 0.0]
        camera.translation = [0.0, 0.0, 0.0]

    response = client.post("/extrinsic/validate")
    assert response.status_code == 200
    assert response.json()["step"] == "export"
    assert load_session(tmp_path, "default").step.value == "export"


def test_list_sessions_summaries(tmp_path: Path) -> None:
    client = _client(tmp_path)

    # No session has been touched yet: nothing on disk.
    assert client.get("/sessions").json() == []

    # Touch the default session (created on first access) -> empty status.
    client.get("/session")
    summaries = client.get("/sessions").json()
    assert len(summaries) == 1
    assert summaries[0]["session_id"] == "default"
    assert summaries[0]["camera_count"] == 0
    assert summaries[0]["status"] == "empty"

    # After configuring cameras -> in_progress with the right count.
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [
                {
                    "index": 0,
                    "device_path": "/dev/v4l/by-path/cam-a",
                    "device_node": "/dev/video0",
                    "width": 1920,
                    "height": 1080,
                    "resize_factor": 1.0,
                    "fps": 30,
                }
            ],
        },
    )
    summaries = client.get("/sessions").json()
    assert summaries[0]["camera_count"] == 1
    assert summaries[0]["status"] == "in_progress"
    assert summaries[0]["step"] == "intrinsic_capture"
    # modified_at is ISO 8601.
    assert "T" in summaries[0]["modified_at"]


def test_capture_view_echoes_the_reported_view(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/capture/view", json={"view": "intrinsic"})
    assert response.status_code == 200
    assert response.json() == {"view": "intrinsic"}


def test_capture_view_accepts_null(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/capture/view", json={"view": None})
    assert response.status_code == 200
    assert response.json() == {"view": None}


def test_preview_routes_without_recording(tmp_path: Path) -> None:
    # ADR-0027: no source recording -> preview 404, status 'missing' (no job).
    client = _client(tmp_path)
    assert client.get("/intrinsic/nope/preview").status_code == 404
    status = client.get("/intrinsic/nope/preview/status")
    assert status.status_code == 200
    assert status.json()["state"] == "missing"
    assert client.get("/extrinsic/cam_0/preview").status_code == 404


def test_preview_serves_the_transcoded_mp4(tmp_path: Path) -> None:
    # The mp4 next to the recording is served as video/mp4 (job mechanics are
    # covered in test_preview.py; here only the HTTP surface).
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    source = manager.intrinsic_video_path("cam_0")
    source.parent.mkdir(parents=True, exist_ok=True)
    preview_path(source).write_bytes(b"not-a-real-mp4")
    served = client.get("/intrinsic/cam_0/preview")
    assert served.status_code == 200
    assert served.headers["content-type"] == "video/mp4"
    assert served.content == b"not-a-real-mp4"


def test_compute_accepts_and_forwards_prepare_knobs(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})
    path = manager.intrinsic_video_path("cam_0")
    with VideoRecorder(path, 64, 48, fps=30) as rec:
        for _ in range(3):
            rec.write(np.zeros((48, 64, 3), dtype=np.uint8))
    # Trim past the 3 recorded frames -> forwarded to the solver stage -> empty range.
    response = client.post("/intrinsic/cam_0/compute", json={"frame_start": 10})
    assert response.status_code == 422
    assert "no readable frames" in response.json()["detail"]


def test_compute_persists_metrics_for_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The whole point of persisting metrics.json: after a reload the Results view is
    # restored from it. Stub the (SIGILL-prone) solver and check the round-trip.
    from calibration_service.calibration.intrinsic import IntrinsicResult
    from calibration_service.transport import api as api_module

    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
    client.post("/board", json={"target": "intrinsic", "board": board})
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [
                {
                    "index": 0,
                    "device_path": "/dev/v4l/by-path/x",
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

    fixture = IntrinsicResult(
        matrix=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        distortions=[0.0] * 8,
        error=0.12,
        per_view_errors=[0.1] * 6,
        grid_count=42,
        view_count=6,
        image_size=(64, 48),
        coverage=((0.0, 1.0), (0.5, 0.0)),
        image_coverage=0.5,
        orientation_bins=6,
        board_quads=(((0.0, 0.0, 10.0), (1.0, 0.0, 10.0), (1.0, 1.0, 10.0), (0.0, 1.0, 10.0)),),
    )
    monkeypatch.setattr(api_module, "compute_intrinsic_from_video", lambda *a, **k: fixture)

    assert client.post("/intrinsic/cam_0/compute").status_code == 200
    # Simulate the reload: fetch the persisted metrics fresh.
    metrics = client.get("/intrinsic/cam_0/metrics").json()
    assert metrics["image_coverage"] == 0.5
    assert metrics["orientation_bins"] == 6
    assert metrics["coverage"] == [[0.0, 1.0], [0.5, 0.0]]
    assert len(metrics["board_quads"]) == 1 and len(metrics["board_quads"][0]) == 4


def test_intrinsic_metrics_endpoint_serves_persisted_payload(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    assert client.get("/intrinsic/cam_0/metrics").status_code == 404  # nothing computed

    path = manager.intrinsic_metrics_path("cam_0")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"coverage": [[0.0, 1.0]], "image_coverage": 0.8, "orientation_bins": 5}')
    response = client.get("/intrinsic/cam_0/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["coverage"] == [[0.0, 1.0]]
    assert body["image_coverage"] == 0.8
    assert body["orientation_bins"] == 5


def _configured_client(
    tmp_path: Path, *, intrinsic_done: bool
) -> tuple[TestClient, SessionManager]:
    """App with 2 configured cameras (+ board), optionally intrinsically calibrated."""
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
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
    if intrinsic_done:
        from calibration_service.models.session import CameraStatus

        for camera in manager.current().cameras:
            camera.status = CameraStatus.INTRINSIC_DONE
    return client, manager


def test_extrinsic_start_rejects_uncalibrated_cameras(tmp_path: Path) -> None:
    client, _ = _configured_client(tmp_path, intrinsic_done=False)
    response = client.post("/extrinsic/start")
    assert response.status_code == 422
    assert "missing intrinsics" in response.json()["detail"]


def test_extrinsic_start_needs_two_cameras(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/extrinsic/start")  # no camera configured
    assert response.status_code == 422
    assert ">= 2 cameras" in response.json()["detail"]


def test_extrinsic_start_503_without_capture_service(tmp_path: Path) -> None:
    # Prerequisites pass but the test app has no publish service wired.
    client, _ = _configured_client(tmp_path, intrinsic_done=True)
    assert client.post("/extrinsic/start").status_code == 503


def test_extrinsic_stop_without_service_returns_empty_counts(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/extrinsic/stop")
    assert response.status_code == 200
    assert response.json() == {"frames": {}}


def test_extrinsic_compute_guards(tmp_path: Path) -> None:
    # No board / no cameras -> 422; with prereqs but no recording -> 404.
    assert _client(tmp_path).post("/extrinsic/compute").status_code == 422
    client, manager = _configured_client(tmp_path, intrinsic_done=True)
    for camera in manager.current().cameras:  # give them intrinsics
        camera.matrix = [[600.0, 0.0, 32.0], [0.0, 600.0, 24.0], [0.0, 0.0, 1.0]]
        camera.distortions = [0.0] * 8
    assert client.post("/extrinsic/compute").status_code == 404  # no manifest


def test_extrinsic_compute_stores_result_and_persists_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from calibration_service.calibration.extrinsic import ExtrinsicResult
    from calibration_service.transport import api as api_module

    client, manager = _configured_client(tmp_path, intrinsic_done=True)
    for camera in manager.current().cameras:
        camera.matrix = [[600.0, 0.0, 32.0], [0.0, 600.0, 24.0], [0.0, 0.0, 1.0]]
        camera.distortions = [0.0] * 8
    directory = manager.extrinsic_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text('{"cameras": []}')
    for name in ("cam_0", "cam_1"):  # sidecars: the sync window derives from them
        (directory / f"{name}.timestamps").write_text("0.000000\n0.033000\n0.066000\n")

    fixture = ExtrinsicResult(
        cameras=["cam_0", "cam_1"],
        rotations={"cam_0": [0.0, 0.0, 0.0], "cam_1": [0.01, -0.02, 0.3]},
        translations={"cam_0": [0.0, 0.0, 0.0], "cam_1": [4.2, 0.1, -0.5]},
        per_camera_error={"cam_0": 0.2, "cam_1": 0.3},
        error=0.25,
        pair_errors={"cam_0|cam_1": 0.004},
        group_count=42,
        point_count=1234,
    )
    from calibration_service.calibration.extrinsic import BAInputs

    ba_fixture = BAInputs(
        obs_camera=[0, 1], obs_point=[0, 0], obs_norm=[[0.1, 0.2], [0.3, 0.4]],
        obs_px=[[10.0, 20.0], [30.0, 40.0]], point_corner=[0],
    )
    monkeypatch.setattr(
        api_module, "compute_extrinsic_from_sweep", lambda *a, **k: (fixture, ba_fixture)
    )

    response = client.post("/extrinsic/compute", json={"stride": 2, "max_spread_ms": 12.0})
    assert response.status_code == 200
    cameras = {c["name"]: c for c in response.json()["cameras"]}
    assert cameras["cam_1"]["rotation"] == [0.01, -0.02, 0.3]
    assert cameras["cam_1"]["translation"] == [4.2, 0.1, -0.5]
    assert cameras["cam_1"]["extrinsic_error"] == 0.3
    assert cameras["cam_0"]["status"] == "extrinsic_done"

    served = client.get("/extrinsic/result")
    assert served.status_code == 200
    body = served.json()
    assert body["error"] == 0.25
    assert body["pair_errors"] == {"cam_0|cam_1": 0.004}
    assert body["group_count"] == 42


def test_extrinsic_result_404_before_compute(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/extrinsic/result").status_code == 404


def test_extrinsic_groups_and_frame_server(tmp_path: Path) -> None:
    from calibration_service.recording import CameraSpec, ExtrinsicRecorder

    client, manager = _configured_client(tmp_path, intrinsic_done=True)
    assert client.get("/extrinsic/groups").status_code == 404  # nothing recorded

    directory = manager.extrinsic_dir()
    recorder = ExtrinsicRecorder(
        directory, [CameraSpec("cam_0", 64, 48, 30), CameraSpec("cam_1", 64, 48, 30)]
    )
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    for g in range(3):  # aligned timestamps -> 3 clean groups
        recorder.write("cam_0", frame, g / 30.0)
        recorder.write("cam_1", frame, g / 30.0 + 0.002)
    recorder.close()

    body = client.get("/extrinsic/groups").json()
    assert body["total"] == 3
    assert len(body["groups"]) == 3
    first = body["groups"][0]
    assert first["frames"] == {"cam_0": 0, "cam_1": 0}
    assert first["spread_ms"] == 2.0

    # Stride keeps every other group; a tight spread filter keeps them all (2 ms).
    strided = client.get("/extrinsic/groups", params={"stride": 2}).json()
    assert len(strided["groups"]) == 2
    filtered = client.get("/extrinsic/groups", params={"max_spread_ms": 1.0}).json()
    assert filtered["groups"] == []

    # The scrubber's frame sources are now the per-camera previews (ADR-0027);
    # their transcode lifecycle is covered in test_preview.py.
