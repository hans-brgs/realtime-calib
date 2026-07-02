"""Owns the current calibration session in memory and persists it (ADR-0011).

Phase 1: a single active session (``default``). Loaded from disk on first
access if present, else created. Mutations are persisted immediately.
"""

from __future__ import annotations

import logging
from pathlib import Path

from calibration_service.calibration import IntrinsicResult
from calibration_service.capture.enumeration import enumerate_camera_devices
from calibration_service.models.board import CalibrationBoard
from calibration_service.models.camera import CameraDevice
from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
    SessionSummary,
    WizardStep,
    session_status,
)
from calibration_service.recording import intrinsic_capture_path
from calibration_service.session.config_store import load_board_config, save_board_config
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
        """Return the active session, loading from disk or creating it on first access.

        Boards live in ``config.toml`` and are merged into the session on load.
        """
        if self._session is None:
            exists = (session_dir(self._sessions_dir, self._session_id) / SESSION_FILE).is_file()
            if exists:
                session = load_session(self._sessions_dir, self._session_id)
                intrinsic, extrinsic = load_board_config(self._sessions_dir, self._session_id)
                session.intrinsic_board = intrinsic
                session.extrinsic_board = extrinsic
            else:
                session = create_session(self._sessions_dir, self._session_id)
            self._session = session
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

    def define_board(self, target: str, board: CalibrationBoard) -> CalibrationSession:
        """Set the intrinsic or extrinsic board, advance the FSM, and persist config.toml.

        ``target`` is ``"intrinsic"`` or ``"extrinsic"``. Defining the intrinsic
        board completes Target Config and unlocks Camera Setup (board-first flow).
        """
        session = self.current()
        if target == "intrinsic":
            session.intrinsic_board = board
            if session.step in (WizardStep.INTRINSIC_BOARD, WizardStep.EXTRINSIC_BOARD_CHOICE):
                session.step = WizardStep.CAMERA_SETUP
        elif target == "extrinsic":
            session.extrinsic_board = board
        else:
            raise ValueError(f"unknown board target: {target!r}")

        save_board_config(
            self._sessions_dir, self._session_id, session.intrinsic_board, session.extrinsic_board
        )
        save_session(self._sessions_dir, session)
        logger.info("defined %s board; step -> %s", target, session.step)
        return session

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir

    def intrinsic_video_path(self, camera_name: str) -> Path:
        """Path of the recorded capture for a camera's intrinsic sweep."""
        return intrinsic_capture_path(self._sessions_dir, self._session_id, camera_name)

    def set_intrinsic_result(
        self, camera_name: str, result: IntrinsicResult
    ) -> CalibrationSession:
        """Store a computed intrinsic result on the camera and mark it done."""
        session = self.current()
        camera = next((c for c in session.cameras if c.name == camera_name), None)
        if camera is None:
            raise ValueError(f"unknown camera {camera_name!r}")
        camera.matrix = result.matrix
        camera.distortions = result.distortions
        camera.calibration_error = result.error
        camera.grid_count = result.grid_count
        camera.status = CameraStatus.INTRINSIC_DONE
        save_session(self._sessions_dir, session)
        logger.info("intrinsic result stored for %s (rms=%.3f)", camera_name, result.error)
        return session

    def configure_cameras(self, cameras: list[CameraConfig]) -> CalibrationSession:
        """Set the session's cameras, advance to intrinsic capture, and persist."""
        session = self.current()
        session.cameras = cameras
        session.step = WizardStep.INTRINSIC_CAPTURE
        save_session(self._sessions_dir, session)
        logger.info("configured %d camera(s); step -> %s", len(cameras), session.step)
        return session
