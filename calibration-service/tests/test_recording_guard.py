"""No reconfiguration while a recording runs: 409 ``recording`` (ADR-0050).

A sweep survives view changes and its recorder's folder is fixed at start: a
camera rebuild or another session would send it frames that are not its own.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.config import LiveKitConfig
from calibration_service.session.manager import SessionManager
from calibration_service.transport.camera_publish_service import CameraPublishService


def _client(tmp_path: Path, *, recording: str | None) -> TestClient:
    manager = SessionManager(tmp_path, "default")
    app = create_app(manager)
    service = CameraPublishService(LiveKitConfig(), manager)

    async def no_refresh() -> None:  # never start the real publisher: it would probe the rig
        pass

    service.refresh = no_refresh  # type: ignore[method-assign]
    if recording == "extrinsic":
        service._extrinsic = object()  # type: ignore[assignment]
    elif recording == "intrinsic":
        service._recorder = object()  # type: ignore[assignment]
    app.state.publish_service = service
    return TestClient(app)


@pytest.mark.parametrize("recording", ["extrinsic", "intrinsic"])
@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("/cameras/config", {"prefix": "cam", "cameras": []}),
        ("/sessions", {"session_id": "other"}),
        ("/sessions/open", {"session_id": "default"}),
    ],
)
def test_reconfiguring_during_a_recording_is_refused(
    tmp_path: Path, recording: str, route: str, body: dict[str, object]
) -> None:
    response = _client(tmp_path, recording=recording).post(route, json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "recording"


def test_importing_during_a_recording_is_refused(tmp_path: Path) -> None:
    response = _client(tmp_path, recording="extrinsic").post(
        "/sessions/import",
        data={"session_id": "imported"},
        files={"file": ("archive.zip", io.BytesIO(b"not read"), "application/zip")},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "recording"


def test_without_a_recording_the_routes_pass(tmp_path: Path) -> None:
    client = _client(tmp_path, recording=None)
    assert client.post("/cameras/config", json={"prefix": "cam", "cameras": []}).status_code == 200
    assert client.post("/sessions", json={"session_id": "other"}).status_code == 200
    assert client.post("/sessions/open", json={"session_id": "default"}).status_code == 200
