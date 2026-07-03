"""Calibration session state: the wizard FSM + per-camera config (ADR-0011/0016).

Owned and persisted by the service ([[calibration-session]]). These models are
mutable (the session evolves) and live in the asyncio orchestrator — not crossing
the process boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from calibration_service.models.board import CalibrationBoard


class WizardStep(StrEnum):
    """Current wizard step (FSM state, see wizard-navigation).

    Board definition comes first so the operator can print early, before wiring
    cameras (ADR-0020 workflow).
    """

    ENTRY = "entry"
    INTRINSIC_BOARD = "intrinsic_board"
    EXTRINSIC_BOARD_CHOICE = "extrinsic_board_choice"
    CAMERA_SETUP = "camera_setup"
    INTRINSIC_CAPTURE = "intrinsic_capture"
    # No REVIEW_3D step: the 3D review is the Result sub-step of the extrinsic
    # capture (spec 3d-extrinsic-review), not a wizard stage of its own.
    EXTRINSIC_CAPTURE = "extrinsic_capture"
    EXPORT = "export"


class SessionMode(StrEnum):
    """How the session was entered (ADR-0019 entry-branching).

    Two modes replace the former ``new`` / ``resume`` / ``load_intrinsic`` /
    ``load_full``: a live capture that always records to disk, or loading an
    existing session folder whose wizard state is derived from its artifacts.
    """

    NEW_REALTIME = "new-realtime"
    LOAD_FROM_FILES = "load-from-files"


class CameraStatus(StrEnum):
    """Per-camera progress (monotone, see Camera entity)."""

    DETECTED = "detected"
    CONFIGURED = "configured"
    INTRINSIC_DONE = "intrinsic_done"
    EXTRINSIC_DONE = "extrinsic_done"


@dataclass
class CameraConfig:
    """Per-camera configuration persisted in the session (pre-calibration)."""

    index: int  # logical index (0 = anchor, ADR-0012)
    name: str  # = f"{prefix}_{index}"
    prefix: str
    device_path: str  # stable by-path identity
    device_node: str  # resolved /dev/videoN
    width: int  # native calibration resolution
    height: int
    resize_factor: float  # output scale s (1.0 = native output), ADR-0015
    fps: int
    status: CameraStatus = CameraStatus.CONFIGURED
    # Intrinsic calibration result (None until computed; camera-array-config fields).
    matrix: list[list[float]] | None = None  # 3x3 K
    distortions: list[float] | None = None  # rational-model coefficients
    calibration_error: float | None = None  # RMS reprojection error (px)
    grid_count: int | None = None  # corners used across keyframes
    # Extrinsic calibration result (ADR-0023; camera-array-config fields). The pose
    # maps world (anchor camera) coords -> this camera's coords; anchor = identity.
    rotation: list[float] | None = None  # Rodrigues 3-vector
    translation: list[float] | None = None  # board-square units until export scaling
    extrinsic_error: float | None = None  # RMS reprojection error after BA (px)


@dataclass
class CalibrationSession:
    """The full wizard state needed to resume after interruption.

    Boards live in ``config.toml`` (loaded/merged by the session manager); the
    rest of this state is persisted in ``session.toml`` (ADR-0016).
    """

    session_id: str
    step: WizardStep = WizardStep.INTRINSIC_BOARD
    mode: SessionMode = SessionMode.NEW_REALTIME
    cameras: list[CameraConfig] = field(default_factory=list)
    intrinsic_fps: int = 30
    optimization_strategy: str = "coverage-aware"
    intrinsic_board: CalibrationBoard | None = None
    extrinsic_board: CalibrationBoard | None = None

    def effective_extrinsic_board(self) -> CalibrationBoard | None:
        """Extrinsic board, inheriting the intrinsic one when not set explicitly."""
        return self.extrinsic_board or self.intrinsic_board


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight session listing entry (dashboard "recent sessions")."""

    session_id: str
    modified_at: float  # epoch seconds (session.toml mtime)
    camera_count: int
    step: WizardStep
    status: str  # "empty" | "in_progress" | "complete"


def session_status(session: CalibrationSession) -> str:
    """Derive a coarse status from session data (for the dashboard listing)."""
    if not session.cameras:
        return "empty"
    if session.step == WizardStep.EXPORT:
        return "complete"
    return "in_progress"
