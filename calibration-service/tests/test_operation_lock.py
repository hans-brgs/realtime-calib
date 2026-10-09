"""One long or mutating operation at a time, service-wide: 409 ``busy`` (ADR-0050)."""

from __future__ import annotations

import threading
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.calibration.intrinsic import IntrinsicResult
from calibration_service.recording import VideoRecorder
from calibration_service.session.manager import SessionManager
from calibration_service.transport import api as api_module

CHARUCO = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
RESULT = IntrinsicResult(
    matrix=[[101.0, 0.0, 32.0], [0.0, 101.0, 24.0], [0.0, 0.0, 1.0]],
    distortions=[0.0] * 5,
    error=0.2,
    per_view_errors=[0.2] * 6,
    grid_count=42,
    view_count=6,
    image_size=(64, 48),
)


def _client(tmp_path: Path) -> tuple[TestClient, SessionManager]:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    client.post("/board", json={"target": "intrinsic", "board": CHARUCO})
    client.post(
        "/board",
        json={
            "target": "extrinsic",
            "board": {**CHARUCO, "square_size_mm": 40.0},
            "inherited": True,
        },
    )
    camera = {
        "index": 0,
        "device_path": "/dev/v4l/by-path/cam0",
        "device_node": "/dev/video0",
        "width": 64,
        "height": 48,
        "fps": 30,
    }
    assert (
        client.post("/cameras/config", json={"prefix": "cam", "cameras": [camera]}).status_code
        == 200
    )
    with VideoRecorder(manager.intrinsic_video_path("cam_0"), 64, 48, fps=30) as recorder:
        recorder.write(np.zeros((48, 64, 3), np.uint8))
    return client, manager


def _post_within(
    client: TestClient, route: str, body: object, seconds: float = 3.0
) -> httpx.Response:
    """POST from a daemon thread: a request that WAITS for the slot fails the test."""
    result: list[httpx.Response] = []
    thread = threading.Thread(
        target=lambda: result.append(client.post(route, json=body)), daemon=True
    )
    thread.start()
    thread.join(seconds)
    if not result:
        pytest.fail(f"{route} waited for the operation slot instead of being refused")
    return result[0]


def test_a_second_operation_during_a_compute_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path)
    started, release = threading.Event(), threading.Event()

    def blocking_compute(*_args: object, **_kwargs: object) -> IntrinsicResult:
        started.set()
        release.wait(5)
        return RESULT

    monkeypatch.setattr(api_module, "compute_intrinsic_from_video", blocking_compute)
    first: dict[str, int] = {}
    worker = threading.Thread(
        target=lambda: first.update(status=client.post("/intrinsic/cam_0/compute").status_code)
    )
    worker.start()
    try:
        assert started.wait(5)
        for route, body in (
            ("/intrinsic/cam_0/compute", None),
            ("/sessions/open", {"session_id": "default"}),
            ("/board", {"target": "intrinsic", "board": CHARUCO}),
            ("/cameras/config", {"prefix": "cam", "cameras": []}),
        ):
            refused = _post_within(client, route, body)
            assert refused.status_code == 409, route
            assert refused.json()["detail"]["code"] == "busy"
            assert "an intrinsic compute is running" in refused.json()["detail"]["message"]
        # Reads and the idempotent stops stay free.
        assert client.get("/session").status_code == 200
        assert client.post("/intrinsic/cam_0/stop").status_code == 200
    finally:
        release.set()
        worker.join(5)
    assert first == {"status": 200}
    # Released once it ended: the next operation runs.
    assert client.post("/intrinsic/cam_0/compute").status_code == 200


def test_a_failed_operation_releases_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path)

    def failing_compute(*_args: object, **_kwargs: object) -> IntrinsicResult:
        raise ValueError("not enough views")

    monkeypatch.setattr(api_module, "compute_intrinsic_from_video", failing_compute)
    assert client.post("/intrinsic/cam_0/compute").status_code == 422
    assert client.post("/board", json={"target": "intrinsic", "board": CHARUCO}).status_code == 200


# Every long or mutating route holds the slot (ADR-0050); a forgotten decorator
# would let it run beside a compute.
_EXCLUSIVE_ROUTES = {
    "/sessions",
    "/sessions/open",
    "/sessions/import",
    "/cameras/config",
    "/intrinsic/{camera}/start",
    "/intrinsic/{camera}/compute",
    "/extrinsic/start",
    "/extrinsic/compute",
    "/extrinsic/orient",
    "/extrinsic/minimize",
    "/board",
}


def test_every_long_or_mutating_route_holds_the_operation_slot() -> None:
    posts = {
        route.path: route.endpoint
        for route in api_module.router.routes
        if isinstance(route, APIRoute) and "POST" in (route.methods or set())
    }
    wrapped = {path for path, endpoint in posts.items() if hasattr(endpoint, "__wrapped__")}
    assert wrapped == _EXCLUSIVE_ROUTES
    for free in ("/intrinsic/{camera}/stop", "/extrinsic/stop"):
        assert free in posts and free not in wrapped  # idempotent stops stay free
