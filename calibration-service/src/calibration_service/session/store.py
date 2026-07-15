"""Read/write the session folder on disk (ADR-0016).

Layout (per session):
    <sessions_dir>/<session_id>/
    ├── session.toml      # this state (atomic write)
    ├── intrinsic/
    └── extrinsic/

``session.toml`` is written atomically (temp file + ``os.replace``); a transition
is persisted before it is considered acquired (ADR-0011).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import rtoml

from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
    SessionMode,
    WizardStep,
)
from calibration_service.tuning import TUNING

logger = logging.getLogger(__name__)

SESSION_FILE = "session.toml"
_INTRINSIC_DIR = "intrinsic"
_EXTRINSIC_DIR = "extrinsic"

# Map pre-ADR-0019 session modes to the two current ones, so old session.toml
# files on disk still reload. Live capture folded into new-realtime; the loaded
# variants into load-from-files.
_LEGACY_MODES: dict[str, SessionMode] = {
    "new": SessionMode.NEW_REALTIME,
    "resume": SessionMode.NEW_REALTIME,
    "load_intrinsic": SessionMode.LOAD_FROM_FILES,
    "load_full": SessionMode.LOAD_FROM_FILES,
}


def _parse_mode(raw: str) -> SessionMode:
    """Parse a persisted mode value, mapping legacy names (ADR-0019)."""
    if raw in _LEGACY_MODES:
        return _LEGACY_MODES[raw]
    return SessionMode(raw)


def session_dir(sessions_dir: Path, session_id: str) -> Path:
    return sessions_dir / session_id


def create_session(
    sessions_dir: Path, session_id: str, mode: SessionMode = SessionMode.NEW_REALTIME
) -> CalibrationSession:
    """Create the session folder structure and persist a fresh session."""
    target = session_dir(sessions_dir, session_id)
    (target / _INTRINSIC_DIR).mkdir(parents=True, exist_ok=True)
    (target / _EXTRINSIC_DIR).mkdir(parents=True, exist_ok=True)
    session = CalibrationSession(session_id=session_id, mode=mode)
    save_session(sessions_dir, session)
    return session


def save_session(sessions_dir: Path, session: CalibrationSession) -> None:
    """Persist ``session.toml`` atomically (temp file + rename)."""
    target = session_dir(sessions_dir, session.session_id)
    target.mkdir(parents=True, exist_ok=True)
    tmp = target / (SESSION_FILE + ".tmp")
    tmp.write_text(rtoml.dumps(_to_dict(session)))
    os.replace(tmp, target / SESSION_FILE)


def load_session(sessions_dir: Path, session_id: str) -> CalibrationSession:
    """Read ``session.toml`` back into a ``CalibrationSession``."""
    path = session_dir(sessions_dir, session_id) / SESSION_FILE
    return _from_dict(rtoml.loads(path.read_text()))


def list_sessions(sessions_dir: Path) -> list[str]:
    """List session ids (folders containing a ``session.toml``)."""
    if not sessions_dir.is_dir():
        return []
    return sorted(
        p.name for p in sessions_dir.iterdir() if (p / SESSION_FILE).is_file()
    )


def session_mtime(sessions_dir: Path, session_id: str) -> float:
    """Last-modified time of ``session.toml`` (epoch seconds)."""
    return (session_dir(sessions_dir, session_id) / SESSION_FILE).stat().st_mtime


def _camera_to_dict(c: CameraConfig) -> dict[str, object]:
    data: dict[str, object] = {
        "index": c.index,
        "name": c.name,
        "prefix": c.prefix,
        "device_path": c.device_path,
        "device_node": c.device_node,
        "width": c.width,
        "height": c.height,
        "resize_factor": c.resize_factor,
        "fps": c.fps,
        "status": c.status.value,
    }
    # Calibration results are optional; omit when absent (rtoml has no null).
    if c.matrix is not None:
        data["matrix"] = c.matrix
    if c.distortions is not None:
        data["distortions"] = c.distortions
    if c.calibration_error is not None:
        data["calibration_error"] = c.calibration_error
    if c.grid_count is not None:
        data["grid_count"] = c.grid_count
    if c.rotation is not None:
        data["rotation"] = c.rotation
    if c.translation is not None:
        data["translation"] = c.translation
    if c.extrinsic_error is not None:
        data["extrinsic_error"] = c.extrinsic_error
    return data


def _camera_from_dict(c: Mapping[str, Any]) -> CameraConfig:
    matrix = c.get("matrix")
    distortions = c.get("distortions")
    error = c.get("calibration_error")
    grid_count = c.get("grid_count")
    rotation = c.get("rotation")
    translation = c.get("translation")
    extrinsic_error = c.get("extrinsic_error")
    return CameraConfig(
        index=int(c["index"]),
        name=str(c["name"]),
        prefix=str(c["prefix"]),
        device_path=str(c["device_path"]),
        device_node=str(c["device_node"]),
        width=int(c["width"]),
        height=int(c["height"]),
        resize_factor=float(c["resize_factor"]),
        fps=int(c["fps"]),
        status=CameraStatus(c["status"]),
        matrix=[[float(v) for v in row] for row in matrix] if matrix is not None else None,
        distortions=[float(v) for v in distortions] if distortions is not None else None,
        calibration_error=float(error) if error is not None else None,
        grid_count=int(grid_count) if grid_count is not None else None,
        rotation=[float(v) for v in rotation] if rotation is not None else None,
        translation=[float(v) for v in translation] if translation is not None else None,
        extrinsic_error=float(extrinsic_error) if extrinsic_error is not None else None,
    )


def _to_dict(session: CalibrationSession) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "step": session.step.value,
        "mode": session.mode.value,
        "export_units": session.export_units,
        "export_targets": list(session.export_targets),
        "cameras": [_camera_to_dict(c) for c in session.cameras],
    }


def _from_dict(data: Mapping[str, Any]) -> CalibrationSession:
    cameras = [_camera_from_dict(c) for c in data.get("cameras", [])]
    return CalibrationSession(
        session_id=str(data["session_id"]),
        step=WizardStep(data["step"]),
        mode=_parse_mode(str(data["mode"])),
        cameras=cameras,
        # Unknown keys in older session.toml files (e.g. the removed
        # intrinsic_fps/optimization_strategy) are simply ignored.
        export_units=str(data.get("export_units", TUNING.export_units)),
        export_targets=[str(t) for t in data.get("export_targets", [])],
    )
