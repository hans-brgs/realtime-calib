"""Smoke test for the HTTP skeleton: the health endpoint answers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from calibration_service import __version__
from calibration_service.app import create_app


def test_health_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "calibration-service",
        "version": __version__,
    }
