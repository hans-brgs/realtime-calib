"""Owns the current calibration session in memory and persists it (ADR-0011).

Phase 1: a single active session (``default``). Loaded from disk on first
access if present, else created. Mutations are persisted immediately.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from calibration_service.calibration import ExtrinsicResult, IntrinsicResult
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
from calibration_service.recording import extrinsic_dir, intrinsic_capture_path
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

# session_id becomes a folder name under sessions_dir: first char alphanumeric
# (forbids ".", "..", hidden dirs), then [A-Za-z0-9._-], max 64. Validated
# service-side — never trust the client with a path segment (ADR-0028).
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class NoActiveSessionError(RuntimeError):
    """Raised by session-scoped operations when no session is active (ADR-0028)."""


def validate_session_id(session_id: str) -> str:
    """Validate a session id destined to become a folder name (ADR-0028).

    Public: the pre-recorded session import (ADR-0031) reuses this exact
    security boundary instead of duplicating it.
    """
    sid = session_id.strip()
    if not _SESSION_ID_RE.fullmatch(sid):
        raise ValueError(
            "session name must start with a letter or digit and use only letters, "
            "digits, '.', '_' or '-' (max 64 characters)"
        )
    return sid


class SessionManager:
    """Single-owner of the *active* calibration session (ADR-0028).

    Starts with **no** active session (``session_id=None``); the operator picks one
    via ``create`` / ``open``. An explicit id keeps the auto-create-on-access
    convenience (used by tests).
    """

    def __init__(self, sessions_dir: Path, session_id: str | None = None) -> None:
        self._sessions_dir = sessions_dir
        self._session_id = session_id
        self._session: CalibrationSession | None = None

    def reorder_cameras(self, device_paths: list[str]) -> CalibrationSession:
        """Permute camera indices to match the given device order and persist.

        Lightweight companion to ``configure_cameras`` (which REBUILDS the
        configs and drops calibrations): here each camera keeps its identity and
        calibration — they belong to the physical device — and only ``index``
        (anchor = 0, ADR-0012) and the position-based ``name`` change. Note:
        recordings already on disk stay keyed by the OLD names (reordering is a
        pre-calibration gesture in the wizard).
        """
        session = self.current()
        by_path = {c.device_path: c for c in session.cameras}
        if set(device_paths) != set(by_path) or len(device_paths) != len(by_path):
            raise ValueError("device paths do not match the configured cameras")
        for position, path in enumerate(device_paths):
            camera = by_path[path]
            camera.index = position
            camera.name = f"{camera.prefix}_{position}"
        session.cameras.sort(key=lambda c: c.index)
        save_session(self._sessions_dir, session)
        logger.info("cameras reordered: %s", ", ".join(device_paths))
        return session

    def sessions_root_label(self) -> str:
        """Host-relative sessions root (compose mounts ./<name>), for the create popup."""
        return self._sessions_dir.name

    def session_dir_label(self) -> str:
        """Host-relative session folder (compose mounts ./<root name> as the root)."""
        if self._session_id is None:
            return self._sessions_dir.name
        return f"{self._sessions_dir.name}/{self._session_id}"

    def create(self, session_id: str) -> CalibrationSession:
        """Create a fresh session folder and make it the active session (ADR-0028).

        Refuses an already-existing folder (the name must be unique) so the
        operator never silently reopens a prior session under 'new'.
        """
        sid = validate_session_id(session_id)
        if (session_dir(self._sessions_dir, sid) / SESSION_FILE).is_file():
            raise FileExistsError(f"session {sid!r} already exists")
        session = create_session(self._sessions_dir, sid)
        self._session_id = sid
        self._session = session
        logger.info("created + activated session %s", sid)
        return session

    def open(self, session_id: str) -> CalibrationSession:
        """Make an existing session the active one (ADR-0028); refuses if absent."""
        sid = validate_session_id(session_id)
        if not (session_dir(self._sessions_dir, sid) / SESSION_FILE).is_file():
            raise FileNotFoundError(f"session {sid!r} not found")
        self._session_id = sid
        self._session = None  # lazy reload (+ boards) through current()
        logger.info("activated session %s", sid)
        return self.current()

    def current_or_none(self) -> CalibrationSession | None:
        """The active session, or ``None`` when none is active (ADR-0028).

        Loads from disk on first access; an explicit id pointing at a missing
        folder is auto-created (test convenience — production never does this).
        Boards live in ``config.toml`` and are merged into the session on load.
        """
        if self._session_id is None:
            return None
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

    def current(self) -> CalibrationSession:
        """The active session, raising ``NoActiveSessionError`` when none is active."""
        session = self.current_or_none()
        if session is None:
            raise NoActiveSessionError("no active session")
        return session

    def _require_session_id(self) -> str:
        """Active session id for path building; raises when none is active."""
        if self._session_id is None:
            raise NoActiveSessionError("no active session")
        return self._session_id

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

    def define_board(
        self, target: str, board: CalibrationBoard | None
    ) -> CalibrationSession:
        """Set the intrinsic or extrinsic board, advance the FSM, and persist config.toml.

        ``target`` is ``"intrinsic"`` or ``"extrinsic"``. Defining the intrinsic board
        advances Target Config to the extrinsic-board choice; confirming the extrinsic
        board — a real board, or ``None`` to inherit the intrinsic one — completes Target
        Config and unlocks Camera Setup (board-first flow, spec wizard-navigation). Both
        steps are walked so the extrinsic choice can't be skipped.
        """
        session = self.current()
        if target == "intrinsic":
            if board is None:
                raise ValueError("intrinsic board is required")
            session.intrinsic_board = board
            if session.step == WizardStep.INTRINSIC_BOARD:
                session.step = WizardStep.EXTRINSIC_BOARD_CHOICE
        elif target == "extrinsic":
            session.extrinsic_board = board  # None = inherit the intrinsic board
            if session.step in (WizardStep.INTRINSIC_BOARD, WizardStep.EXTRINSIC_BOARD_CHOICE):
                session.step = WizardStep.CAMERA_SETUP
        else:
            raise ValueError(f"unknown board target: {target!r}")

        save_board_config(
            self._sessions_dir,
            self._require_session_id(),
            session.intrinsic_board,
            session.extrinsic_board,
        )
        save_session(self._sessions_dir, session)
        logger.info("defined %s board; step -> %s", target, session.step)
        return session

    @property
    def sessions_dir(self) -> Path:
        return self._sessions_dir

    def intrinsic_video_path(self, camera_name: str) -> Path:
        """Path of the recorded capture for a camera's intrinsic sweep."""
        return intrinsic_capture_path(self._sessions_dir, self._require_session_id(), camera_name)

    def intrinsic_metrics_path(self, camera_name: str) -> Path:
        """Path of the persisted review metrics (coverage/orientation/poses, ADR-0022)."""
        return self.intrinsic_video_path(camera_name).with_name("metrics.json")

    def extrinsic_dir(self) -> Path:
        """Folder of the synchronized extrinsic sweep (videos + timestamp sidecars)."""
        return extrinsic_dir(self._sessions_dir, self._require_session_id())

    def export_dir(self) -> Path:
        """Folder of the exported calibration artifacts (spec calibration-export)."""
        return session_dir(self._sessions_dir, self._require_session_id()) / "export"

    def mark_exported(self) -> CalibrationSession:
        """Advance the wizard to the export step and persist it."""
        session = self.current()
        session.step = WizardStep.EXPORT
        save_session(self._sessions_dir, session)
        return session

    def set_export_config(self, units: str, targets: list[str]) -> CalibrationSession:
        """Persist the export config (units + selected targets) so it is restored
        on reopen (ADR-0026). The truth stays result.json; this is a preference."""
        session = self.current()
        session.export_units = units
        session.export_targets = list(targets)
        save_session(self._sessions_dir, session)
        return session

    def begin_extrinsic_capture(self) -> CalibrationSession:
        """Advance the wizard to the extrinsic capture step and persist it."""
        session = self.current()
        session.step = WizardStep.EXTRINSIC_CAPTURE
        save_session(self._sessions_dir, session)
        logger.info("extrinsic capture started; step -> %s", session.step)
        return session

    def set_extrinsic_result(self, result: ExtrinsicResult) -> CalibrationSession:
        """Store the solved array poses on the cameras and mark them done (ADR-0023)."""
        session = self.current()
        for camera in session.cameras:
            rotation = result.rotations.get(camera.name)
            if rotation is None:
                continue  # solver refuses unreachable cameras upstream (guard-rail)
            camera.rotation = rotation
            camera.translation = result.translations[camera.name]
            camera.extrinsic_error = result.per_camera_error.get(camera.name)
            camera.status = CameraStatus.EXTRINSIC_DONE
        save_session(self._sessions_dir, session)
        logger.info(
            "extrinsic result stored for %d camera(s) (rms=%.3f px)",
            len(result.cameras),
            result.error,
        )
        return session

    def set_intrinsic_result(
        self, camera_name: str, result: IntrinsicResult
    ) -> CalibrationSession:
        """Store a computed intrinsic result on the camera and mark it done."""
        session = self.current()
        camera = next((c for c in session.cameras if c.name == camera_name), None)
        if camera is None:
            raise ValueError(f"unknown camera {camera_name!r}")
        # Calibrated at native resolution; report at the operator's output resolution
        # (native x resize_factor, ADR-0015).
        result = result.scaled(camera.resize_factor)
        camera.matrix = result.matrix
        camera.distortions = result.distortions
        camera.calibration_error = result.error
        # Caliscope semantics (ADR-0002): grid_count = number of boards/views
        # used by the solve, not the corner total (which stays on the result).
        camera.grid_count = result.view_count
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

    def confirm_camera_setup(self) -> CalibrationSession:
        """Advance past Camera Setup WITHOUT rebuilding the camera configs.

        Load-from-files flow (ADR-0031): the cameras derive from the imported
        videos — there is nothing to re-detect, and ``configure_cameras`` would
        drop them. Confirming just unlocks Intrinsics. Idempotent once past;
        refuses to skip the board steps (wizard order, spec wizard-navigation).
        """
        session = self.current()
        if not session.cameras:
            raise ValueError("no cameras to confirm")
        if session.step in (
            WizardStep.ENTRY,
            WizardStep.INTRINSIC_BOARD,
            WizardStep.EXTRINSIC_BOARD_CHOICE,
        ):
            raise ValueError("define the calibration boards first")
        if session.step == WizardStep.CAMERA_SETUP:
            session.step = WizardStep.INTRINSIC_CAPTURE
            save_session(self._sessions_dir, session)
            logger.info("camera setup confirmed; step -> %s", session.step)
        return session
