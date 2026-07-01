"""Calibration session state: the wizard FSM + per-camera config (ADR-0011/0016).

Owned and persisted by the service ([[calibration-session]]). These models are
mutable (the session evolves) and live in the asyncio orchestrator — not crossing
the process boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class WizardStep(StrEnum):
    """Current wizard step (FSM state, see wizard-navigation)."""

    ENTRY = "entry"
    CAMERA_SETUP = "camera_setup"
    INTRINSIC_BOARD = "intrinsic_board"
    EXTRINSIC_BOARD_CHOICE = "extrinsic_board_choice"
    INTRINSIC_CAPTURE = "intrinsic_capture"
    EXTRINSIC_CAPTURE = "extrinsic_capture"
    REVIEW_3D = "review_3d"
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


@dataclass
class CalibrationSession:
    """The full wizard state needed to resume after interruption."""

    session_id: str
    step: WizardStep = WizardStep.CAMERA_SETUP
    mode: SessionMode = SessionMode.NEW_REALTIME
    cameras: list[CameraConfig] = field(default_factory=list)
    intrinsic_fps: int = 30
    optimization_strategy: str = "coverage-aware"


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
