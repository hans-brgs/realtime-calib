"""Rig-level operator settings: store persistence + API round-trip (ADR-0036)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.session.manager import SessionManager
from calibration_service.settings import RuntimeSettings, SettingsStore
from calibration_service.tuning import TUNING


def test_store_defaults_from_tuning_when_no_file(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    assert store.current.record_quality == TUNING.record_quality
    assert store.current.preview_fps == TUNING.preview_fps


def test_store_replace_persists_and_reloads(tmp_path: Path) -> None:
    SettingsStore(tmp_path).replace(RuntimeSettings(record_quality=90, preview_fps=15))

    reloaded = SettingsStore(tmp_path)  # a fresh service restart
    assert reloaded.current.record_quality == 90
    assert reloaded.current.preview_fps == 15


def test_store_omits_follow_camera_fps_from_toml(tmp_path: Path) -> None:
    # TOML has no null: "follow the camera fps" is an absent key.
    SettingsStore(tmp_path).replace(RuntimeSettings(record_quality=95, preview_fps=None))
    text = (tmp_path / "settings.toml").read_text()
    assert "preview_fps" not in text

    assert SettingsStore(tmp_path).current.preview_fps is None


def test_store_survives_a_corrupt_file(tmp_path: Path) -> None:
    (tmp_path / "settings.toml").write_text("not [valid { toml")
    store = SettingsStore(tmp_path)
    assert store.current == RuntimeSettings()  # TUNING defaults, no crash


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(SessionManager(tmp_path, "default")))


def test_settings_api_round_trip(tmp_path: Path) -> None:
    client = _client(tmp_path)

    body = client.get("/settings").json()
    assert body == {"record_quality": TUNING.record_quality, "preview_fps": TUNING.preview_fps}

    response = client.put("/settings", json={"record_quality": 88, "preview_fps": 15})
    assert response.status_code == 200
    assert client.get("/settings").json() == {"record_quality": 88, "preview_fps": 15}
    # Persisted next to the sessions root (survives a service restart).
    assert (tmp_path / "settings.toml").is_file()

    # Back to "follow the camera fps".
    client.put("/settings", json={"record_quality": 88, "preview_fps": None})
    assert client.get("/settings").json()["preview_fps"] is None


def test_settings_api_rejects_out_of_bounds(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.put("/settings", json={"record_quality": 50}).status_code == 422
    assert (
        client.put("/settings", json={"record_quality": 95, "preview_fps": 60}).status_code == 422
    )
