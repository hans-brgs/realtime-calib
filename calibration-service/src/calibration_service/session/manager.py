"""Owns the current calibration session in memory and persists it (ADR-0011).

Phase 1: a single active session (``default``). Loaded from disk on first
access if present, else created. Mutations are persisted immediately.
"""

from __future__ import annotations

import logging
from pathlib import Path

from calibration_service.capture.enumeration import enumerate_camera_devices
from calibration_service.models.camera import CameraDevice
from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    SessionSummary,
    WizardStep,
    session_status,
)
from calibration_service.session.store import (
    SESSION_FILE,
    create_session,
    list_sessions,
    load_session,
    save_session,
    session_dir,
    session_mtime,
)

logger = logging.getLogger(__name__)

DEFAULT_SESSION_ID = "default"


class SessionManager:
    """Single-owner of the active calibration session."""

    def __init__(self, sessions_dir: Path, session_id: str = DEFAULT_SESSION_ID) -> None:
        self._sessions_dir = sessions_dir
        self._session_id = session_id
        self._session: CalibrationSession | None = None

    def current(self) -> CalibrationSession:
        """Return the active session, loading from disk or creating it on first access."""
        if self._session is None:
            exists = (session_dir(self._sessions_dir, self._session_id) / SESSION_FILE).is_file()
            self._session = (
                load_session(self._sessions_dir, self._session_id)
                if exists
                else create_session(self._sessions_dir, self._session_id)
            )
        return self._session

    def detect(self) -> list[CameraDevice]:
        """Enumerate connected cameras with their full set of modes."""
        return enumerate_camera_devices()

    def summaries(self) -> list[SessionSummary]:
        """List all persisted sessions as lightweight summaries, most recent first."""
        result: list[SessionSummary] = []
        for session_id in list_sessions(self._sessions_dir):
            try:
                session = load_session(self._sessions_dir, session_id)
            except Exception:
                logger.exception("skipping unreadable session %s", session_id)
                continue
            result.append(
                SessionSummary(
                    session_id=session_id,
                    modified_at=session_mtime(self._sessions_dir, session_id),
                    camera_count=len(session.cameras),
                    step=session.step,
                    status=session_status(session),
                )
            )
        return sorted(result, key=lambda s: s.modified_at, reverse=True)

    def configure_cameras(self, cameras: list[CameraConfig]) -> CalibrationSession:
        """Set the session's cameras, advance past camera setup, and persist."""
        session = self.current()
        session.cameras = cameras
        session.step = WizardStep.INTRINSIC_BOARD
        save_session(self._sessions_dir, session)
        logger.info("configured %d camera(s); step -> %s", len(cameras), session.step)
        return session
