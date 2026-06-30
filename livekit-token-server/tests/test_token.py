"""Tests for token issuance: claims are correct and decodable with the secret."""

from __future__ import annotations

import jwt

from livekit_token_server.app import create_app
from livekit_token_server.config import Settings

SETTINGS = Settings(
    livekit_api_key="devkey",
    livekit_api_secret="test-secret-at-least-32-bytes-long-xyz",
    room_name="calibration",
    port=8080,
)


def test_health_ok() -> None:
    client = create_app(SETTINGS).test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_token_carries_requested_identity_and_room() -> None:
    client = create_app(SETTINGS).test_client()

    response = client.get("/token?identity=alice&room=room-1")

    assert response.status_code == 200
    body = response.get_json()
    assert body["identity"] == "alice"
    assert body["room"] == "room-1"

    claims = jwt.decode(body["token"], SETTINGS.livekit_api_secret, algorithms=["HS256"])
    assert claims["sub"] == "alice"
    assert claims["video"]["room"] == "room-1"
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["canSubscribe"] is True
    # Viewer tokens must not be able to publish.
    assert claims["video"].get("canPublish") in (False, None)


def test_token_defaults_to_configured_room_and_generated_identity() -> None:
    client = create_app(SETTINGS).test_client()

    body = client.get("/token").get_json()

    assert body["room"] == "calibration"
    assert body["identity"].startswith("viewer-")


def test_token_sets_cors_header() -> None:
    client = create_app(SETTINGS).test_client()
    response = client.get("/token")
    assert response.headers["Access-Control-Allow-Origin"] == "*"
