"""Tests for the HTTP API: session, camera detection (mocked), configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.models.camera import CameraDevice, CameraMode, Resolution
from calibration_service.session.manager import SessionManager
from calibration_service.session.store import load_session


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(SessionManager(tmp_path)))


def test_get_session_returns_fresh_session(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/session")

    assert response.status_code == 200
    body = response.json()
    assert body["step"] == "camera_setup"
    assert body["mode"] == "new-realtime"
    assert body["cameras"] == []


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
    assert body["step"] == "intrinsic_board"
    assert [c["name"] for c in body["cameras"]] == ["cam_0", "cam_1"]

    # Persisted to disk: a fresh load reflects the config.
    reloaded = load_session(tmp_path, "default")
    assert len(reloaded.cameras) == 2
    assert reloaded.cameras[0].name == "cam_0"
    assert reloaded.cameras[0].status.value == "configured"


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
    assert summaries[0]["step"] == "intrinsic_board"
    # modified_at is ISO 8601.
    assert "T" in summaries[0]["modified_at"]
