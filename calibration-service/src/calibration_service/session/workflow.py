"""A session's extrinsic solve, shared by the API and the offline tools (QLT-17).

The transport layer validates a request and answers it; what a solve reads from the
session (the native camera models, the knobs resolved against TUNING, ADR-0036) and
how its errors are reported (output pixels, ADR-0042) lives here, so a tool that
re-solves a recorded session gets exactly what the service would compute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from calibration_service.calibration.extrinsic import (
    BAInputs,
    CameraModel,
    ExtrinsicResult,
    compute_extrinsic_from_sweep,
    derive_sweep_window,
)
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.session import CalibrationSession, CameraConfig
from calibration_service.resolution import to_native
from calibration_service.tuning import TUNING


@dataclass(frozen=True)
class SweepSettings:
    """The extrinsic compute's knobs, every one resolved (ADR-0036)."""

    stride: int
    max_groups: int
    min_shared: int
    max_spread_s: float | None  # None: derived from the recorded cadence
    max_motion_px: float | None  # None: the motion gate off (ADR-0056)


def sweep_settings(
    board: CalibrationBoard,
    *,
    stride: int | None = None,
    max_groups: int | None = None,
    min_shared: int | None = None,
    max_spread_ms: float | None = None,
    max_motion_px: float | None = None,
    motion_gate: bool = True,
) -> SweepSettings:
    """Omitted knobs against TUNING, per target type; an explicit value is kept verbatim."""
    charuco = board.board_type is BoardType.CHARUCO
    return SweepSettings(
        stride=stride
        if stride is not None
        else (TUNING.extrinsic_stride_charuco if charuco else TUNING.extrinsic_stride_marker),
        max_groups=max_groups
        if max_groups is not None
        else (TUNING.max_groups_charuco if charuco else TUNING.max_groups_marker),
        min_shared=min_shared if min_shared is not None else TUNING.min_shared,
        max_spread_s=max_spread_ms / 1000.0 if max_spread_ms is not None else None,
        max_motion_px=(
            (max_motion_px if max_motion_px is not None else TUNING.extrinsic_max_motion_px)
            if motion_gate
            else None
        ),
    )


def native_camera_model(camera: CameraConfig) -> CameraModel:
    """Solver intrinsics at the RECORDING resolution (undo the ADR-0015 scaling)."""
    if camera.matrix is None or camera.distortions is None:
        raise ValueError(f"{camera.name} has no intrinsics; calibrate it first")
    return CameraModel(
        name=camera.name,
        matrix=to_native(camera.matrix, (camera.width, camera.height), camera.resize_factor or 1.0),
        distortions=np.asarray(camera.distortions, np.float64),
    )


def output_scaled_errors(result: ExtrinsicResult, session: CalibrationSession) -> ExtrinsicResult:
    """Extrinsic pixel errors at the operator's output resolution (ADR-0042).

    The solver reports at the native recording resolution; every operator-facing
    surface (session state, result.json, webapp) speaks output pixels — the same
    contract the intrinsic path applies via ``IntrinsicResult.scaled`` (ADR-0015).
    Applied exactly once, on the compute and minimize exits; reorientation carries
    the already-scaled errors through untouched.
    """
    return result.scaled_errors(
        {camera.name: camera.resize_factor or 1.0 for camera in session.cameras}
    )


def solve_extrinsics(
    directory: Path,
    session: CalibrationSession,
    board: CalibrationBoard,
    settings: SweepSettings,
) -> tuple[ExtrinsicResult, BAInputs]:
    """Solve the recorded sweep under ``directory`` (errors in native px).

    The anchor is the lowest-index camera (ADR-0012); the sync window comes from the
    RECORDED cadence (sidecars), not the configured fps, which the effective write
    rate can sit well below (ADR-0007). Raises ValueError on an unsolvable sweep.
    """
    anchor = min(session.cameras, key=lambda c: c.index)
    models = [native_camera_model(c) for c in session.cameras]
    window_s = derive_sweep_window(directory, [c.name for c in session.cameras])
    return compute_extrinsic_from_sweep(
        directory,
        board,
        models,
        anchor=anchor.name,
        window_s=window_s,
        stride=settings.stride,
        max_groups=settings.max_groups,
        max_spread_s=settings.max_spread_s,
        min_shared=settings.min_shared,
        max_motion_px=settings.max_motion_px,
    )
