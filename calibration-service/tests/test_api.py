"""Tests for the HTTP API: session, camera detection (mocked), configuration."""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.models.camera import CameraDevice, CameraMode, Resolution
from calibration_service.models.session import WizardStep
from calibration_service.recording import VideoRecorder, preview_path
from calibration_service.session.manager import SessionManager
from calibration_service.session.store import create_session, load_session, save_session

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(SessionManager(tmp_path, "default")))


def _import_zip(tmp_path: Path, tree: dict[str, int]) -> bytes:
    """An in-memory upload archive: {relpath: frame count} rendered via VideoRecorder."""
    stage = tmp_path / "zip-stage"
    for rel, frames in tree.items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with VideoRecorder(target, 64, 48, fps=30) as rec:
            for _ in range(frames):
                rec.write(np.zeros((48, 64, 3), dtype=np.uint8))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                bundle.write(file, str(file.relative_to(stage)))
    return buffer.getvalue()


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


@requires_ffmpeg
def test_import_session_ingests_and_activates(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    client = TestClient(create_app(SessionManager(sessions)))
    payload = _import_zip(
        tmp_path,
        {
            "intrinsics/cam_00.mkv": 6,
            "intrinsics/cam_01.mkv": 6,
            "extrinsics/cam_00.mkv": 5,
            "extrinsics/cam_01.mkv": 5,
        },
    )

    response = client.post(
        "/sessions/import",
        files={"file": ("myset.zip", payload, "application/zip")},
        data={"session_id": "myset"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "load-from-files"
    assert body["step"] == "intrinsic_board"  # lands on Target Config
    assert [c["name"] for c in body["cameras"]] == ["cam_0", "cam_1"]
    # Imported session is now the active one; canonical artifacts are on disk.
    assert client.get("/session").json()["session_id"] == "myset"
    assert (sessions / "myset/intrinsic/cam_0/capture.mkv").is_file()
    assert (sessions / "myset/extrinsic/manifest.json").is_file()
    # The upload spool is cleaned up (dot-prefixed temp next to the sessions).
    assert not [p for p in sessions.iterdir() if p.name.startswith(".import-")]


def test_import_session_duplicate_name_is_409(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    create_session(sessions, "taken")
    client = TestClient(create_app(SessionManager(sessions)))
    response = client.post(
        "/sessions/import",
        files={"file": ("x.zip", b"irrelevant", "application/zip")},
        data={"session_id": "taken"},
    )
    assert response.status_code == 409


def test_import_session_bad_naming_is_422(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("intrinsics/webcam.mkv", "fake")
    client = TestClient(create_app(SessionManager(tmp_path / "sessions")))
    response = client.post(
        "/sessions/import",
        files={"file": ("b.zip", buffer.getvalue(), "application/zip")},
        data={"session_id": "bad-name-set"},
    )
    assert response.status_code == 422
    assert "cam_<number>" in response.json()["detail"]


def test_import_session_unreadable_archive_is_400(tmp_path: Path) -> None:
    client = TestClient(create_app(SessionManager(tmp_path / "sessions")))
    response = client.post(
        "/sessions/import",
        files={"file": ("n.zip", b"not a zip at all", "application/zip")},
        data={"session_id": "notzip"},
    )
    assert response.status_code == 400


def test_confirm_camera_setup_route_maps_errors(tmp_path: Path) -> None:
    # No active session -> uniform 409 (ADR-0028); active but no cameras -> 422.
    bare = TestClient(create_app(SessionManager(tmp_path / "a")))
    assert bare.post("/cameras/confirm").status_code == 409
    fresh = _client(tmp_path / "b")
    assert fresh.post("/cameras/confirm").status_code == 422


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
    # Config no longer advances the wizard (ADR-0040): the operator verifies,
    # iterates, then moves on via /cameras/confirm.
    assert body["step"] == "camera_setup"
    assert [c["name"] for c in body["cameras"]] == ["cam_0", "cam_1"]

    # Persisted to disk: a fresh load reflects the config.
    reloaded = load_session(tmp_path, "default")
    assert len(reloaded.cameras) == 2
    assert reloaded.cameras[0].name == "cam_0"
    assert reloaded.cameras[0].status.value == "configured"


def test_reconfigure_regresses_the_wizard_and_reorder_persists(tmp_path: Path) -> None:
    # ADR-0040: /cameras/config is the ONE write path (order included) and never
    # advances; a rebuild from a later step regresses to camera_setup because
    # the fresh configs carry no calibration — the downstream steps just lost
    # their prerequisites. /cameras/confirm is the only way forward.
    def cam(index: int, path: str) -> dict[str, object]:
        return {
            "index": index,
            "device_path": path,
            "device_node": f"/dev/video{index}",
            "width": 1280,
            "height": 720,
            "resize_factor": 1.0,
            "fps": 30,
        }

    client = _client(tmp_path)
    client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [cam(0, "/dev/v4l/by-path/cam-a"), cam(1, "/dev/v4l/by-path/cam-b")],
        },
    )
    assert client.post("/cameras/confirm").json()["step"] == "intrinsic_capture"

    # Simulate a calibrated session past camera setup.
    manager = SessionManager(tmp_path, "default")
    session = manager.current()
    session.cameras[0].matrix = [[800.0, 0.0, 640.0], [0.0, 800.0, 360.0], [0.0, 0.0, 1.0]]
    session.step = WizardStep.EXTRINSIC_CAPTURE
    save_session(tmp_path, session)

    # Re-apply with the two cameras SWAPPED (a drag-reorder, via the same path).
    body = client.post(
        "/cameras/config",
        json={
            "prefix": "cam",
            "cameras": [cam(0, "/dev/v4l/by-path/cam-b"), cam(1, "/dev/v4l/by-path/cam-a")],
        },
    ).json()
    assert body["step"] == "camera_setup"  # regressed: prerequisites are gone
    assert [c["device_path"] for c in body["cameras"]] == [
        "/dev/v4l/by-path/cam-b",
        "/dev/v4l/by-path/cam-a",
    ]
    assert [c["name"] for c in body["cameras"]] == ["cam_0", "cam_1"]
    assert all(c["matrix"] is None for c in body["cameras"])  # rebuild = recalibrate

    # The new order survives a reload.
    reloaded = load_session(tmp_path, "default")
    assert [c.device_path for c in reloaded.cameras] == [
        "/dev/v4l/by-path/cam-b",
        "/dev/v4l/by-path/cam-a",
    ]
    assert reloaded.step is WizardStep.CAMERA_SETUP


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
    client.post("/cameras/confirm")
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


def test_capture_view_accepts_idle(tmp_path: Path) -> None:
    # 'idle' is the explicit "release all" for a non-capturing screen (D7.3).
    response = _client(tmp_path).post("/capture/view", json={"view": "idle"})
    assert response.status_code == 200
    assert response.json() == {"view": "idle"}


def test_capture_view_rejects_unknown_id(tmp_path: Path) -> None:
    # D7.3: an unknown view id (a typo, or the old 'extrinsic-idle' magic string) is now
    # rejected with 422 instead of silently mapping to "no camera".
    response = _client(tmp_path).post("/capture/view", json={"view": "extrinsic-idle"})
    assert response.status_code == 422


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


def test_intrinsic_stop_without_service_returns_zero_frames(tmp_path: Path) -> None:
    # stop is an idempotent no-op (unlike start's 503): no capture service wired
    # still resolves to 200 with a zero count, so teardown never errors.
    response = _client(tmp_path).post("/intrinsic/cam_0/stop")
    assert response.status_code == 200
    assert response.json() == {"camera": "cam_0", "frames": 0}


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

    response = client.post("/extrinsic/compute", json={"max_groups": 120, "max_spread_ms": 12.0})
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


def test_orient_persists_the_framed_group_marker(tmp_path: Path) -> None:
    # "Set frame on board" records WHICH group carried the gesture (the review
    # scrubber marker): set by set_frame, kept through rotate, served on reload.
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    board = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 8, "rows": 5}
    client.post("/board", json={"target": "intrinsic", "board": board})
    directory = manager.extrinsic_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fixture = {
        "cameras": ["cam_0", "cam_1"],
        "rotations": {"cam_0": [0.0, 0.0, 0.0], "cam_1": [0.0, 0.1, 0.0]},
        "translations": {"cam_0": [0.0, 0.0, 0.0], "cam_1": [1.0, 0.0, 0.0]},
        "per_camera_error": {"cam_0": 0.1, "cam_1": 0.2},
        "error": 0.15,
        "pair_errors": {"cam_0|cam_1": 0.01},
        "group_count": 1,
        "point_count": 4,
        "points": [[0.0, 0.0, 5.0]],
        "point_groups": [0],
        "board_quads": [[[0.0, 0.0, 5.0], [1.0, 0.0, 5.0], [1.0, 1.0, 5.0], [0.0, 1.0, 5.0]]],
    }
    (directory / "result.json").write_text(json.dumps(fixture))

    rotated = client.post("/extrinsic/orient", json={"op": "rotate", "axis": "x", "degrees": 90})
    assert rotated.status_code == 200
    assert rotated.json()["framed_group"] is None  # no gesture yet

    framed = client.post("/extrinsic/orient", json={"op": "set_frame", "group": 0})
    assert framed.status_code == 200
    assert framed.json()["framed_group"] == 0

    rotated = client.post("/extrinsic/orient", json={"op": "rotate", "axis": "y", "degrees": -90})
    assert rotated.json()["framed_group"] == 0  # rotate keeps the marker
    assert client.get("/extrinsic/result").json()["framed_group"] == 0  # survives reload


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

    # A tight spread filter drops every group (2 ms recorded spread > 1 ms cap).
    filtered = client.get("/extrinsic/groups", params={"max_spread_ms": 1.0}).json()
    assert filtered["groups"] == []

    # The scrubber's frame sources are now the per-camera previews (ADR-0027);
    # their transcode lifecycle is covered in test_preview.py.
