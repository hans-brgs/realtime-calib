"""Tests for session persistence (round-trip, folder structure, atomic write)."""

from __future__ import annotations

from pathlib import Path

from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
    SessionMode,
    WizardStep,
)
from calibration_service.session.store import (
    SESSION_FILE,
    create_session,
    list_sessions,
    load_session,
    save_session,
    session_dir,
)


def _sample_camera() -> CameraConfig:
    return CameraConfig(
        index=0,
        name="cam_0",
        prefix="cam",
        device_path="/dev/v4l/by-path/cam-a",
        device_node="/dev/video0",
        width=1920,
        height=1080,
        resize_factor=1 / 3,
        fps=30,
        status=CameraStatus.CONFIGURED,
    )


def test_create_session_makes_folder_structure(tmp_path: Path) -> None:
    create_session(tmp_path, "demo")

    base = session_dir(tmp_path, "demo")
    assert (base / SESSION_FILE).is_file()
    assert (base / "intrinsic").is_dir()
    assert (base / "extrinsic").is_dir()


def test_round_trip_preserves_state(tmp_path: Path) -> None:
    original = CalibrationSession(
        session_id="demo",
        step=WizardStep.EXTRINSIC_CAPTURE,
        mode=SessionMode.LOAD_FROM_FILES,
        cameras=[_sample_camera()],
        intrinsic_fps=60,
        optimization_strategy="outlier-rejection",
    )

    save_session(tmp_path, original)
    loaded = load_session(tmp_path, "demo")

    assert loaded == original


def test_save_is_atomic_no_temp_left(tmp_path: Path) -> None:
    save_session(tmp_path, CalibrationSession(session_id="demo"))

    base = session_dir(tmp_path, "demo")
    assert (base / SESSION_FILE).is_file()
    assert not (base / (SESSION_FILE + ".tmp")).exists()


def test_list_sessions(tmp_path: Path) -> None:
    create_session(tmp_path, "alpha")
    create_session(tmp_path, "beta")
    (tmp_path / "not_a_session").mkdir()  # no session.toml

    assert list_sessions(tmp_path) == ["alpha", "beta"]


def test_reload_maps_legacy_modes(tmp_path: Path) -> None:
    """Pre-ADR-0019 session.toml files still load (mode name mapping)."""
    base = session_dir(tmp_path, "legacy")
    base.mkdir(parents=True)
    (base / SESSION_FILE).write_text(
        'session_id = "legacy"\n'
        'step = "camera_setup"\n'
        'mode = "load_intrinsic"\n'
        "intrinsic_fps = 30\n"
        'optimization_strategy = "coverage-aware"\n'
        "cameras = []\n"
    )

    loaded = load_session(tmp_path, "legacy")

    assert loaded.mode is SessionMode.LOAD_FROM_FILES
