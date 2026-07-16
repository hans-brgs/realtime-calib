"""HTTP API: session rehydration + camera detection/configuration (Phase 1).

Mounted at the service root; Caddy strips the ``/api`` prefix (ADR-0014).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import tempfile
import zipfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import rtoml
from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from calibration_service.board import SUPPORTED_DICTIONARIES, render_board_png, validate_board
from calibration_service.calibration import (
    BAInputs,
    CameraModel,
    ExtrinsicResult,
    axis_rotation_transform,
    board_unit_mm,
    compute_extrinsic_from_sweep,
    compute_intrinsic_from_video,
    derive_sweep_window,
    quad_origin_transform,
    refine_result,
    reorient_result,
    sweep_groups,
)
from calibration_service.export import (
    PLATFORM_FORMATS,
    caliscope_document,
    export_targets,
    platform_variant,
)
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.camera import CameraDevice
from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
)
from calibration_service.recording import (
    PreviewJobs,
    PreviewState,
    PreviewStatus,
    preview_path,
)
from calibration_service.recording.ffmpeg import FfmpegError
from calibration_service.session.import_session import UnreadableArchiveError, ingest
from calibration_service.session.manager import SessionManager
from calibration_service.settings import RuntimeSettings, SettingsStore
from calibration_service.transport.camera_publish_service import CameraPublishService
from calibration_service.tuning import TUNING

router = APIRouter()


@router.get("/defaults")
async def pipeline_defaults() -> dict[str, object]:
    """User-facing pipeline defaults and bounds — backend = single source (ADR-0036).

    The webapp seeds its inputs (values AND min/max) from this payload instead of
    hardcoding copies; the values live in ``calibration_service.tuning``.
    """
    return asdict(TUNING)


class SettingsPayload(BaseModel):
    """Rig-level operator settings (ADR-0036): full-replace PUT semantics.

    Both fields are always sent (the webapp holds the complete current value from
    GET), so there is no partial-update ambiguity. ``preview_fps = null`` means
    "follow the camera fps". Changes apply live: the capture loops re-read the
    settings (publication pacer swaps on the next frame; recording quality
    applies to the next recording).
    """

    record_quality: int = Field(
        ge=TUNING.record_quality_bounds[0],
        le=TUNING.record_quality_bounds[1],
    )
    preview_fps: int | None = Field(
        default=None,
        ge=1,
        le=max(TUNING.fps_options),
    )


def get_settings_store(request: Request) -> SettingsStore:
    store = request.app.state.settings
    assert isinstance(store, SettingsStore)
    return store


@router.get("/settings")
async def get_settings(request: Request) -> SettingsPayload:
    current = get_settings_store(request).current
    return SettingsPayload(
        record_quality=current.record_quality, preview_fps=current.preview_fps
    )


@router.put("/settings")
async def put_settings(request: Request, body: SettingsPayload) -> SettingsPayload:
    """Persist the operator settings to <sessions_dir>/settings.toml (ADR-0036)."""
    get_settings_store(request).replace(
        RuntimeSettings(record_quality=body.record_quality, preview_fps=body.preview_fps)
    )
    return body


# --- Schemas -----------------------------------------------------------------


class ModeOut(BaseModel):
    pixel_format: str
    width: int
    height: int
    fps: list[float]


class DetectedCameraOut(BaseModel):
    index: int
    device_path: str
    device_node: str
    modes: list[ModeOut]


class CameraConfigIn(BaseModel):
    index: int
    device_path: str
    device_node: str
    width: int
    height: int
    # Output-contract scale (ADR-0015): 1.0 = native; the offered list is TUNING's.
    resize_factor: float = Field(default=1.0, gt=0.0, le=1.0)
    # The ladder max IS the recording cap (ADR-0037: one cadence per camera).
    fps: int = Field(ge=1, le=max(TUNING.fps_options))


class ConfigRequest(BaseModel):
    prefix: str
    cameras: list[CameraConfigIn]


class CameraConfigOut(BaseModel):
    index: int
    name: str
    prefix: str
    device_path: str
    device_node: str
    width: int
    height: int
    resize_factor: float
    fps: int
    status: str
    matrix: list[list[float]] | None = None
    distortions: list[float] | None = None
    calibration_error: float | None = None
    grid_count: int | None = None
    rotation: list[float] | None = None
    translation: list[float] | None = None
    extrinsic_error: float | None = None


class BoardIn(BaseModel):
    """Board definition; omitted fields resolve to the TUNING defaults (ADR-0036).

    Semantic coherence (ratio vs sizes, dictionary capacity...) stays in
    ``validate_board`` — bounds here only reject the structurally impossible.
    """

    board_type: str
    dictionary: str
    columns: int = Field(default=TUNING.board.columns, ge=2, le=30)
    rows: int = Field(default=TUNING.board.rows, ge=2, le=30)
    marker_ratio: float = Field(default=TUNING.board.marker_ratio, gt=0.0, lt=1.0)
    marker_id: int = Field(default=TUNING.board.marker_id, ge=0)
    square_size_mm: float = Field(default=TUNING.board.square_size_mm, gt=0.0)
    marker_size_mm: float = Field(default=TUNING.board.marker_size_mm, gt=0.0)
    inverted: bool = TUNING.board.inverted


class BoardConfigRequest(BaseModel):
    target: Literal["intrinsic", "extrinsic"]
    # None = inherit the intrinsic board (extrinsic only); required for intrinsic.
    board: BoardIn | None = None


class BoardOut(BoardIn):
    pass


class IssueOut(BaseModel):
    """One actionable load-time anomaly (ADR-0036 fail-loud): the wizard stage to
    revisit (webapp rail id) and a human message. The webapp shows a banner and a
    badge on that rail step."""

    step: str
    message: str


class SessionOut(BaseModel):
    session_id: str
    session_dir: str = ""  # host-relative session folder (compose mounts ./sessions)
    step: str
    mode: str
    issues: list[IssueOut] = []  # transient: recomputed at every session load
    export_units: str  # persisted export config (ADR-0026), restored on reopen
    export_targets: list[str] = []
    cameras: list[CameraConfigOut]
    intrinsic_board: BoardOut | None
    extrinsic_board: BoardOut | None


class SessionSummaryOut(BaseModel):
    session_id: str
    modified_at: str  # ISO 8601 UTC
    camera_count: int
    step: str
    status: str


# --- Converters ---------------------------------------------------------------


def _device_out(device: CameraDevice) -> DetectedCameraOut:
    return DetectedCameraOut(
        index=device.index,
        device_path=device.device_path,
        device_node=device.device_node,
        modes=[
            ModeOut(
                pixel_format=m.pixel_format,
                width=m.resolution.width,
                height=m.resolution.height,
                fps=list(m.fps),
            )
            for m in device.modes
        ],
    )


def _board_out(board: CalibrationBoard | None) -> BoardOut | None:
    if board is None:
        return None
    return BoardOut(
        board_type=board.board_type,
        dictionary=board.dictionary,
        columns=board.columns,
        rows=board.rows,
        marker_ratio=board.marker_ratio,
        marker_id=board.marker_id,
        square_size_mm=board.square_size_mm,
        marker_size_mm=board.marker_size_mm,
        inverted=board.inverted,
    )


def _to_board(item: BoardIn) -> CalibrationBoard:
    return CalibrationBoard(
        board_type=BoardType(item.board_type),
        dictionary=item.dictionary,
        columns=item.columns,
        rows=item.rows,
        marker_ratio=item.marker_ratio,
        marker_id=item.marker_id,
        square_size_mm=item.square_size_mm,
        marker_size_mm=item.marker_size_mm,
        inverted=item.inverted,
    )


def _session_out(session: CalibrationSession, manager: SessionManager) -> SessionOut:
    return SessionOut(
        session_id=session.session_id,
        session_dir=manager.session_dir_label(),
        step=session.step,
        mode=session.mode,
        issues=[IssueOut(step=i.step, message=i.message) for i in session.issues],
        export_units=session.export_units,
        export_targets=list(session.export_targets),
        cameras=[
            CameraConfigOut(
                index=c.index,
                name=c.name,
                prefix=c.prefix,
                device_path=c.device_path,
                device_node=c.device_node,
                width=c.width,
                height=c.height,
                resize_factor=c.resize_factor,
                fps=c.fps,
                status=c.status,
                matrix=c.matrix,
                distortions=c.distortions,
                calibration_error=c.calibration_error,
                grid_count=c.grid_count,
                rotation=c.rotation,
                translation=c.translation,
                extrinsic_error=c.extrinsic_error,
            )
            for c in session.cameras
        ],
        intrinsic_board=_board_out(session.intrinsic_board),
        extrinsic_board=_board_out(session.extrinsic_board),
    )


def _to_camera_config(prefix: str, item: CameraConfigIn) -> CameraConfig:
    return CameraConfig(
        index=item.index,
        name=f"{prefix}_{item.index}",
        prefix=prefix,
        device_path=item.device_path,
        device_node=item.device_node,
        width=item.width,
        height=item.height,
        resize_factor=item.resize_factor,
        fps=item.fps,
        status=CameraStatus.CONFIGURED,
    )


# --- Dependency + routes ------------------------------------------------------


def get_manager(request: Request) -> SessionManager:
    manager = request.app.state.session_manager
    assert isinstance(manager, SessionManager)
    return manager


def get_publish_service(request: Request) -> CameraPublishService | None:
    service = getattr(request.app.state, "publish_service", None)
    return service if isinstance(service, CameraPublishService) else None


@router.get("/session", response_model=SessionOut)
async def get_session(request: Request) -> SessionOut:
    """The active session, or 404 when none is active (ADR-0028) — the webapp maps
    that to a null session (dashboard, rail locked) rather than an error."""
    manager = get_manager(request)
    session = manager.current_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="no active session")
    return _session_out(session, manager)


@router.get("/sessions", response_model=list[SessionSummaryOut])
async def list_sessions_route(request: Request) -> list[SessionSummaryOut]:
    return [
        SessionSummaryOut(
            session_id=s.session_id,
            modified_at=datetime.fromtimestamp(s.modified_at, tz=UTC).isoformat(),
            camera_count=s.camera_count,
            step=s.step,
            status=s.status,
        )
        for s in get_manager(request).summaries()
    ]


@router.get("/sessions/location")
async def sessions_location(request: Request) -> dict[str, str]:
    """Host-relative sessions root, so the create popup can show the target path."""
    return {"root": get_manager(request).sessions_root_label()}


class SessionRef(BaseModel):
    """A session folder name (used as the session id)."""

    session_id: str


@router.post("/sessions", response_model=SessionOut)
async def create_session_route(request: Request, body: SessionRef) -> SessionOut:
    """Create a fresh session with a unique folder name and make it active (ADR-0028).

    409 if the name already exists, 422 if it is not a valid folder name. The new
    session is empty (step ``intrinsic_board``); ``refresh()`` re-syncs the live
    cameras (the M4-hardened teardown) onto it.
    """
    manager = get_manager(request)
    try:
        session = manager.create(body.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    service = get_publish_service(request)
    if service is not None:
        await service.refresh()
    return _session_out(session, manager)


@router.post("/sessions/open", response_model=SessionOut)
async def open_session_route(request: Request, body: SessionRef) -> SessionOut:
    """Make an existing session the active one (ADR-0028). 404 if it does not exist."""
    manager = get_manager(request)
    try:
        session = manager.open(body.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    service = get_publish_service(request)
    if service is not None:
        await service.refresh()
    return _session_out(session, manager)


@router.post("/sessions/import", response_model=SessionOut)
async def import_session_route(
    request: Request,
    file: UploadFile,
    session_id: str = Form(),
) -> SessionOut:
    """Import a pre-recorded session archive (ZIP or tar) and activate it (ADR-0031).

    The archive is spooled to disk next to the sessions (same volume as the
    destination), then the synchronous ingest (extract + validate + remux +
    sidecars) runs in an executor — the event loop keeps serving. 409 if the
    session name exists, 422 on a contract violation (naming/format/sync/media),
    400 on an unreadable archive. On success the imported session becomes the
    active one and its preview transcodes are kicked off in the background.
    """
    manager = get_manager(request)
    sessions_dir = manager.sessions_dir
    loop = asyncio.get_running_loop()

    def _spool_to_disk() -> Path:
        sessions_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=sessions_dir, prefix=".import-upload-", suffix=".archive", delete=False
        ) as spool:
            shutil.copyfileobj(file.file, spool)
        return Path(spool.name)

    await file.seek(0)
    archive = await loop.run_in_executor(None, _spool_to_disk)
    try:
        try:
            imported = await loop.run_in_executor(
                None, lambda: ingest(archive, session_id, sessions_dir)
            )
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except UnreadableArchiveError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ValueError, FfmpegError) as exc:
            # ImportValidationError, bad session name, or media ffmpeg can't process.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await loop.run_in_executor(None, lambda: archive.unlink(missing_ok=True))

    session = manager.open(imported.session_id)  # activate (ADR-0028 switch semantics)
    jobs = get_preview_jobs(request)
    for camera in session.cameras:
        # Kick the background preview transcodes NOW (ADR-0027) so the Prepare
        # scrubber is usually ready by the time the operator reaches it; ensure()
        # is a no-op for a phase whose recording is absent.
        jobs.ensure(manager.intrinsic_video_path(camera.name))
        jobs.ensure(manager.extrinsic_dir() / f"{camera.name}.mkv")
    service = get_publish_service(request)
    if service is not None:
        await service.refresh()
    return _session_out(session, manager)


@router.post("/cameras/detect", response_model=list[DetectedCameraOut])
async def detect_cameras(request: Request) -> list[DetectedCameraOut]:
    return [_device_out(d) for d in get_manager(request).detect()]


@router.post("/cameras/config", response_model=SessionOut)
async def configure_cameras(request: Request, body: ConfigRequest) -> SessionOut:
    configs = [_to_camera_config(body.prefix, item) for item in body.cameras]
    manager = get_manager(request)
    session = manager.configure_cameras(configs)

    # Reactive republish: apply the new config to the live LiveKit tracks (option a).
    publish_service = get_publish_service(request)
    if publish_service is not None:
        await publish_service.refresh()

    return _session_out(session, manager)


@router.post("/cameras/confirm", response_model=SessionOut)
async def confirm_camera_setup_route(request: Request) -> SessionOut:
    """Advance past Camera Setup without rebuilding the configs (ADR-0031).

    Load-from-files flow: the cameras derive from the imported videos, so
    /cameras/config (which rebuilds and drops them) is not applicable — this
    just unlocks Intrinsics. 422 with no cameras or before the boards are set.
    """
    manager = get_manager(request)
    try:
        session = manager.confirm_camera_setup()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session_out(session, manager)


@router.get("/board/dictionaries", response_model=list[str])
async def board_dictionaries() -> list[str]:
    return list(SUPPORTED_DICTIONARIES)


class ActiveIntrinsicRequest(BaseModel):
    camera: str | None  # track name (e.g. "cam_0"), or null to stop


@router.post("/intrinsic/active")
async def set_active_intrinsic(request: Request, body: ActiveIntrinsicRequest) -> dict[str, object]:
    """Select which camera runs board detection/overlay/telemetry (Phase 3.5)."""
    service = get_publish_service(request)
    if service is not None:
        service.set_active_intrinsic(body.camera)
    return {"active": body.camera}


# Capture-view id (ADR-0021): a wizard stage, the transient 'load' sub-flow, or 'idle'
# (explicit "release all"). Must match the webapp CaptureView union. None = not reported
# yet -> publish all (safe default); an unknown id is rejected (422) instead of silently
# mapping to no camera.
CaptureView = Literal[
    "session", "cameras", "boards", "intrinsic", "extrinsic", "export", "load", "review", "idle"
]


class CaptureViewRequest(BaseModel):
    view: CaptureView | None = None


@router.post("/capture/view")
async def set_capture_view(request: Request, body: CaptureViewRequest) -> dict[str, object]:
    """Report the operator's current wizard view; drives the live camera set (ADR-0021).

    On-demand capture: the service opens/publishes only the cameras this view needs
    (``cameras``/``extrinsic`` → all, ``intrinsic`` → the active camera, else none).
    """
    service = get_publish_service(request)
    if service is not None:
        service.set_active_view(body.view)
    return {"view": body.view}


@router.post("/intrinsic/{camera}/start")
async def start_intrinsic(request: Request, camera: str) -> dict[str, object]:
    """Begin recording the intrinsic sweep of ``camera`` (record → compute → review)."""
    service = get_publish_service(request)
    if service is None:
        raise HTTPException(status_code=503, detail="capture service unavailable")
    # Surface "no active session" as the uniform 409 (ADR-0028) BEFORE the broad
    # except below, which would otherwise mask NoActiveSessionError as a 422.
    get_manager(request).current()
    try:
        await service.start_intrinsic_recording(camera)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"recording": camera}


@router.post("/intrinsic/{camera}/stop")
async def stop_intrinsic(request: Request, camera: str) -> dict[str, object]:
    """Finalise the recording; the video is ready to compute.

    Idempotent no-op: returns a zero frame count when nothing is recording or
    the capture service is unavailable. Unlike ``start`` (which needs the service
    and 503s without it), ``stop`` only guarantees "nothing is recording" — a
    state already met when the service is absent, so the webapp can call it
    defensively on teardown without provoking spurious errors.
    """
    service = get_publish_service(request)
    frames = await service.stop_intrinsic_recording() if service is not None else 0
    return {"camera": camera, "frames": frames}


def get_preview_jobs(request: Request) -> PreviewJobs:
    jobs = request.app.state.preview_jobs
    assert isinstance(jobs, PreviewJobs)
    return jobs


def _preview_status_out(status: PreviewStatus) -> dict[str, object]:
    return {
        "state": status.state.value,
        "frames": status.frames,
        # Index <-> time rate of the DONE preview (dynamic contract, ADR-0037).
        "fps": status.fps,
        # Cache-buster the webapp appends to the preview URL (stale-video guard).
        "version": status.version,
        "error": status.error,
    }


@router.get("/intrinsic/{camera}/preview")
async def intrinsic_preview(request: Request, camera: str) -> FileResponse:
    """The CFR-retimed H.264 preview of the recorded sweep (ADR-0027)."""
    path = preview_path(get_manager(request).intrinsic_video_path(camera))
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no preview for {camera}")
    return FileResponse(path, media_type="video/mp4")


@router.get("/intrinsic/{camera}/preview/status")
async def intrinsic_preview_status(request: Request, camera: str) -> dict[str, object]:
    """Transcode state (auto-enqueues a missing preview when the source exists)."""
    source = get_manager(request).intrinsic_video_path(camera)
    return _preview_status_out(await get_preview_jobs(request).status(source))


@router.post("/intrinsic/{camera}/preview/transcode")
async def intrinsic_preview_retry(request: Request, camera: str) -> dict[str, object]:
    """Explicitly relaunch a failed transcode (webapp Retry button)."""
    source = get_manager(request).intrinsic_video_path(camera)
    jobs = get_preview_jobs(request)
    jobs.retry(source)
    return _preview_status_out(await jobs.status(source))


class ComputeRequest(BaseModel):
    """Prepare-step knobs (ADR-0022); omitted fields resolve to TUNING (ADR-0036)."""

    # "Detect 1 frame every N" within the trim (read decimation).
    stride: int | None = Field(
        default=None,
        ge=TUNING.intrinsic_stride_bounds[0],
        le=TUNING.intrinsic_stride_bounds[1],
    )
    # Keyframes kept for the solve.
    cap: int | None = Field(
        default=None,
        ge=TUNING.intrinsic_cap_bounds[0],
        le=TUNING.intrinsic_cap_bounds[1],
    )
    frame_start: int = Field(default=0, ge=0)  # trim start (frame index)
    frame_end: int | None = Field(default=None, ge=1)  # trim end (exclusive)


@router.post("/intrinsic/{camera}/compute", response_model=SessionOut)
async def compute_intrinsic(
    request: Request, camera: str, body: ComputeRequest | None = None
) -> SessionOut:
    """Compute intrinsics from the recorded sweep and store them (Phase 3.7).

    The optional body carries the Prepare-step knobs (stride/cap/trim, ADR-0022).
    Reads the video off the capture loop (executor); the capture is released
    (overlay/telemetry off) while the solver runs (ADR-0019).
    """
    params = body or ComputeRequest()
    manager = get_manager(request)
    board = manager.current().intrinsic_board
    if board is None:
        raise HTTPException(status_code=422, detail="no intrinsic board defined")

    service = get_publish_service(request)
    if service is not None:
        await service.stop_intrinsic_recording()
        service.set_active_intrinsic(None)

    path = manager.intrinsic_video_path(camera)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no recording for {camera}")

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None,
            lambda: compute_intrinsic_from_video(
                path,
                board,
                cap=params.cap if params.cap is not None else TUNING.intrinsic_cap,
                stride=params.stride
                if params.stride is not None
                else TUNING.intrinsic_stride,
                frame_start=params.frame_start,
                frame_end=params.frame_end,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session = manager.set_intrinsic_result(camera, result)
    # Persist the review metrics next to the recording so the Results view survives a
    # reload/resume (ADR-0022); all fields are resolution-independent.
    metrics = {
        "coverage": [list(row) for row in result.coverage],
        "image_coverage": result.image_coverage,
        "orientation_bins": result.orientation_bins,
        "board_quads": [[list(point) for point in quad] for quad in result.board_quads],
        # Sharpness of the retained keyframes (ADR-0038): the observability that
        # replaced the absolute blur gate — a blurry sweep succeeds, visibly.
        "sharpness_min": result.sharpness_min,
        "sharpness_median": result.sharpness_median,
    }
    metrics_path = manager.intrinsic_metrics_path(camera)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics))
    return _session_out(session, manager)


@router.post("/intrinsic/validate", response_model=SessionOut)
async def validate_intrinsic(request: Request) -> SessionOut:
    """Operator sign-off on every camera's intrinsics: advance the wizard to the
    extrinsic capture step. The webapp rail follows the persisted step, so this
    single transition IS the navigation (spec wizard-navigation)."""
    manager = get_manager(request)
    session = manager.current()
    if not session.cameras or any(
        c.status not in (CameraStatus.INTRINSIC_DONE, CameraStatus.EXTRINSIC_DONE)
        for c in session.cameras
    ):
        raise HTTPException(status_code=422, detail="intrinsic calibration incomplete")
    return _session_out(manager.begin_extrinsic_capture(), manager)


@router.get("/intrinsic/{camera}/metrics")
async def intrinsic_metrics(request: Request, camera: str) -> dict[str, object]:
    """Serve the persisted review metrics for the Results view (ADR-0022).

    ``{coverage: quad-accumulation count map (ADR-0039), image_coverage: union-area
    fraction, orientation_bins: /8, board_quads: per-keyframe 4x3 board outline in
    camera coords, sharpness_min/median: retained-keyframe sharpness (ADR-0038)}``.
    """
    path = get_manager(request).intrinsic_metrics_path(camera)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no metrics for {camera}")
    payload: dict[str, object] = json.loads(path.read_text())
    return payload


@router.post("/extrinsic/start")
async def start_extrinsic(request: Request) -> dict[str, object]:
    """Begin the synchronized multi-camera extrinsic sweep (ADR-0007/0023).

    Prerequisites (spec extrinsic-calibration-flow): >= 2 configured cameras, every
    camera intrinsically calibrated, and an (effective) extrinsic board defined.
    """
    manager = get_manager(request)
    session = manager.current()
    if len(session.cameras) < 2:
        raise HTTPException(status_code=422, detail="extrinsic capture needs >= 2 cameras")
    missing = [
        c.name
        for c in session.cameras
        if c.status not in (CameraStatus.INTRINSIC_DONE, CameraStatus.EXTRINSIC_DONE)
    ]
    if missing:
        raise HTTPException(
            status_code=422, detail=f"cameras missing intrinsics: {', '.join(missing)}"
        )
    if session.effective_extrinsic_board() is None:
        raise HTTPException(status_code=422, detail="no extrinsic board defined")

    service = get_publish_service(request)
    if service is None:
        raise HTTPException(status_code=503, detail="capture service unavailable")
    try:
        await service.start_extrinsic_recording()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    manager.begin_extrinsic_capture()
    return {"recording": True, "cameras": len(session.cameras)}


@router.post("/extrinsic/stop")
async def stop_extrinsic(request: Request) -> dict[str, object]:
    """Finalise the synchronized sweep; per-camera videos + sidecars are on disk.

    Idempotent no-op: returns empty counts when no sweep is running or the capture
    service is unavailable. Unlike ``start`` (which needs the service and 503s
    without it), ``stop`` only guarantees "nothing is recording" — a state already
    met when the service is absent, so the webapp can call it defensively on
    teardown without provoking spurious errors.
    """
    service = get_publish_service(request)
    counts = await service.stop_extrinsic_recording() if service is not None else {}
    return {"frames": counts}


class ExtrinsicComputeRequest(BaseModel):
    """Prepare-step knobs (ADR-0023/0033); omitted fields resolve to TUNING (ADR-0036)."""

    # Detection stride over the spread-filtered candidate groups ("1 group / N",
    # the cost knob, mirroring the intrinsic Prepare); None = board-type default.
    stride: int | None = Field(
        default=None,
        ge=TUNING.extrinsic_stride_bounds[0],
        le=TUNING.extrinsic_stride_bounds[1],
    )
    # Sharpest groups kept for the solve (ADR-0033); None = board-type default.
    max_groups: int | None = Field(
        default=None,
        ge=TUNING.max_groups_bounds[0],
        le=TUNING.max_groups_bounds[1],
    )
    # Drop groups with a larger timestamp spread; None = no filter.
    max_spread_ms: float | None = Field(
        default=None,
        ge=TUNING.max_spread_ms_bounds[0],
        le=TUNING.max_spread_ms_bounds[1],
    )
    # Minimum shared board views per camera pair. API-only knob since ADR-0036
    # (the UI control was removed; possible reintegration under an Advanced
    # section later).
    min_shared: int | None = Field(
        default=None,
        ge=TUNING.min_shared_bounds[0],
        le=TUNING.min_shared_bounds[1],
    )


def _native_camera_model(camera: CameraConfig) -> CameraModel:
    """Solver intrinsics at the RECORDING resolution (undo the ADR-0015 scaling)."""
    factor = camera.resize_factor or 1.0
    matrix = np.asarray(camera.matrix, np.float64).copy()
    matrix[0] /= factor
    matrix[1] /= factor
    return CameraModel(
        name=camera.name,
        matrix=matrix,
        distortions=np.asarray(camera.distortions, np.float64),
    )


@router.post("/extrinsic/compute", response_model=SessionOut)
async def compute_extrinsic(
    request: Request, body: ExtrinsicComputeRequest | None = None
) -> SessionOut:
    """Solve the camera array from the recorded sweep and store it (ADR-0023).

    Pairwise stereo init + transitive chaining from the anchor (camera index 0)
    + bundle adjustment, off the event loop. The full result (poses, pair errors)
    is persisted next to the recordings for the Result 3D view.
    """
    params = body or ExtrinsicComputeRequest()
    manager = get_manager(request)
    session = manager.current()
    board = session.effective_extrinsic_board()
    if board is None:
        raise HTTPException(status_code=422, detail="no extrinsic board defined")
    uncalibrated = [
        c.name for c in session.cameras if c.matrix is None or c.distortions is None
    ]
    if len(session.cameras) < 2 or uncalibrated:
        raise HTTPException(
            status_code=422,
            detail=f"cameras missing intrinsics: {', '.join(uncalibrated) or 'need >= 2'}",
        )
    directory = manager.extrinsic_dir()
    if not (directory / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="no extrinsic recording")

    service = get_publish_service(request)
    if service is not None:
        await service.stop_extrinsic_recording()

    anchor = min(session.cameras, key=lambda c: c.index)  # index 0 = anchor (ADR-0012)
    models = [_native_camera_model(c) for c in session.cameras]
    # Window from the RECORDED cadence (sidecars), not the configured fps: the
    # effective write rate can sit well below it (ADR-0007 intent = real interval).
    try:
        window_s = derive_sweep_window(directory, [c.name for c in session.cameras])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Resolve omitted knobs against TUNING in one place (ADR-0036); an explicit
    # value is honoured verbatim — the Pydantic bounds already rejected nonsense.
    max_spread_s = (
        params.max_spread_ms / 1000.0 if params.max_spread_ms is not None else None
    )
    charuco = board.board_type is BoardType.CHARUCO
    stride = (
        params.stride
        if params.stride is not None
        else (TUNING.extrinsic_stride_charuco if charuco else TUNING.extrinsic_stride_marker)
    )
    max_groups = (
        params.max_groups
        if params.max_groups is not None
        else (TUNING.max_groups_charuco if charuco else TUNING.max_groups_marker)
    )
    min_shared = params.min_shared if params.min_shared is not None else TUNING.min_shared

    loop = asyncio.get_running_loop()
    try:
        result, ba_inputs = await loop.run_in_executor(
            None,
            lambda: compute_extrinsic_from_sweep(
                directory,
                board,
                models,
                anchor=anchor.name,
                window_s=window_s,
                stride=stride,
                max_groups=max_groups,
                max_spread_s=max_spread_s,
                min_shared=min_shared,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = manager.set_extrinsic_result(result)
    (directory / "result.json").write_text(json.dumps(asdict(result)))
    # BA observations: lets Minimize refine later without redetecting the videos.
    (directory / "ba_inputs.json").write_text(json.dumps(asdict(ba_inputs)))
    return _session_out(session, manager)


@router.get("/extrinsic/result")
async def extrinsic_result(request: Request) -> dict[str, object]:
    """Serve the persisted array solve (poses + errors) for the Result 3D view."""
    path = get_manager(request).extrinsic_dir() / "result.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no extrinsic result")
    payload: dict[str, object] = json.loads(path.read_text())
    return payload


class OrientRequest(BaseModel):
    """Reorient the solved world frame (spec 3d-extrinsic-review, mutating).

    ``set_frame`` = the single framing gesture (ADR-0026): world origin on the
    board (marker center / ChArUco c0), axes aligned to it, and the board normal
    put on the export-up axis (-y canonical) so a floor-laid board lands level.
    ``rotate`` reorients ±90° about an axis for any other placement.
    """

    op: Literal["set_frame", "rotate"]
    group: int | None = None  # set_frame: group whose board becomes the frame
    axis: Literal["x", "y", "z"] | None = None  # rotate
    degrees: float | None = None  # rotate (the UI sends +/-90)


def _load_extrinsic_result(manager: SessionManager) -> ExtrinsicResult:
    path = manager.extrinsic_dir() / "result.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no extrinsic result")
    return ExtrinsicResult(**json.loads(path.read_text()))


def _store_extrinsic_result(manager: SessionManager, result: ExtrinsicResult) -> None:
    manager.set_extrinsic_result(result)
    (manager.extrinsic_dir() / "result.json").write_text(json.dumps(asdict(result)))


@router.post("/extrinsic/orient")
async def orient_extrinsic(request: Request, body: OrientRequest) -> dict[str, object]:
    """Apply a rigid world-frame change to the solved array and persist it.

    ``set_frame`` puts the world origin/axes on the board of one group with its
    normal on the up axis; ``rotate`` turns the frame ±90° about an axis.
    Reprojection quality is invariant, so errors carry over; the updated result
    payload is returned.
    """
    manager = get_manager(request)
    result = _load_extrinsic_result(manager)
    if body.op == "set_frame":
        if body.group is None or not (0 <= body.group < len(result.board_quads)):
            raise HTTPException(status_code=422, detail="invalid group")
        quad = result.board_quads[body.group]
        if quad is None:
            raise HTTPException(status_code=422, detail="group has no board pose")
        # Single-ArUco targets: the marker frame sits at its CENTER (cv2
        # convention); a ChArUco board frame originates at its first corner.
        board = manager.current().effective_extrinsic_board()
        marker = board is not None and board.board_type is not BoardType.CHARUCO
        transform = quad_origin_transform(
            quad, at_center=marker, ground=True
        )
    else:
        if body.axis is None or body.degrees is None:
            raise HTTPException(status_code=422, detail="rotate needs axis + degrees")
        transform = axis_rotation_transform(body.axis, body.degrees)
    reoriented = reorient_result(result, transform)
    # Remember which group carried the framing gesture (review-scrubber marker);
    # a rotate keeps the existing marker — the world reference did not move.
    framed = body.group if body.op == "set_frame" else result.framed_group
    reoriented = replace(reoriented, framed_group=framed)
    _store_extrinsic_result(manager, reoriented)
    payload: dict[str, object] = asdict(reoriented)
    return payload


@router.post("/extrinsic/minimize")
async def minimize_extrinsic(request: Request) -> dict[str, object]:
    """Filter outliers + re-run the bundle adjustment (spec 'Minimize').

    Caliscope's quality loop: drops the worst 2.5% of the persisted BA
    observations by current pixel residual, then re-fits (no redetection).
    Repeat-safe: each run re-filters from the FULL persisted set, so clicks
    converge to a fixed point instead of ratcheting data away. The anchor
    keeps its current pose, preserving any operator reorientation.
    """
    manager = get_manager(request)
    session = manager.current()
    board = session.effective_extrinsic_board()
    if board is None:
        raise HTTPException(status_code=422, detail="no extrinsic board defined")
    result = _load_extrinsic_result(manager)
    ba_path = manager.extrinsic_dir() / "ba_inputs.json"
    if not ba_path.is_file():
        raise HTTPException(status_code=404, detail="no BA observations (recompute first)")
    ba_inputs = BAInputs(**json.loads(ba_path.read_text()))
    models = [_native_camera_model(c) for c in session.cameras if c.matrix is not None]
    anchor = min(session.cameras, key=lambda c: c.index)

    loop = asyncio.get_running_loop()
    try:
        refined = await loop.run_in_executor(
            None, lambda: refine_result(result, ba_inputs, models, board, anchor.name)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Minimize keeps the anchor pose, so the framing marker stays meaningful.
    refined = replace(refined, framed_group=result.framed_group)
    _store_extrinsic_result(manager, refined)
    payload: dict[str, object] = asdict(refined)
    return payload


@router.get("/extrinsic/groups")
async def extrinsic_groups(
    request: Request,
    max_spread_ms: float | None = Query(
        default=None,
        ge=TUNING.max_spread_ms_bounds[0],
        le=TUNING.max_spread_ms_bounds[1],
    ),
) -> dict[str, object]:
    """Synchronized groups of the recorded sweep, for the Prepare scrubber (ADR-0023).

    Synchronizes the timestamp sidecars only (no video decoding). This lists the
    spread-filtered CANDIDATES; the compute keeps the sharpest ~max_groups of them
    (ADR-0033 — result.group_count reports how many). The window derives from the
    RECORDED cadence (sidecar median inter-frame delta), not the config fps.
    """
    manager = get_manager(request)
    session = manager.current()
    directory = manager.extrinsic_dir()
    if not (directory / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="no extrinsic recording")
    names = [c.name for c in session.cameras]
    loop = asyncio.get_running_loop()
    try:
        window_s = await loop.run_in_executor(None, derive_sweep_window, directory, names)
        groups = await loop.run_in_executor(None, sweep_groups, directory, names, window_s)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    total = len(groups)
    if max_spread_ms is not None:
        groups = [g for g in groups if g.spread * 1000.0 <= max_spread_ms]
    return {
        "total": total,
        "groups": [
            {
                "frames": {name: frame.payload for name, frame in group.frames.items()},
                "spread_ms": round(group.spread * 1000.0, 2),
            }
            for group in groups
        ],
    }


@router.post("/extrinsic/validate", response_model=SessionOut)
async def validate_extrinsic(request: Request) -> SessionOut:
    """Operator sign-off on the solved array: advance the wizard to Export.

    The webapp's rail follows the persisted step, so this single transition IS
    the navigation (spec wizard-navigation).
    """
    manager = get_manager(request)
    session = manager.current()
    if not session.cameras or any(c.rotation is None for c in session.cameras):
        raise HTTPException(status_code=422, detail="extrinsic calibration incomplete")
    return _session_out(manager.mark_exported(), manager)


@router.get("/extrinsic/preview/status")
async def extrinsic_preview_status(request: Request) -> dict[str, object]:
    """Aggregate transcode state over the sweep's cameras (ADR-0027).

    Auto-enqueues missing previews. Aggregate: failed > running > done; missing
    when no camera has a recording at all.
    """
    manager = get_manager(request)
    jobs = get_preview_jobs(request)
    directory = manager.extrinsic_dir()
    cameras: dict[str, dict[str, object]] = {}
    states: list[PreviewState] = []
    for camera in manager.current().cameras:
        status = await jobs.status(directory / f"{camera.name}.mkv")
        cameras[camera.name] = _preview_status_out(status)
        if status.state is not PreviewState.MISSING:
            states.append(status.state)
    if not states:
        aggregate = PreviewState.MISSING
    elif PreviewState.FAILED in states:
        aggregate = PreviewState.FAILED
    elif PreviewState.RUNNING in states:
        aggregate = PreviewState.RUNNING
    else:
        aggregate = PreviewState.DONE
    return {"state": aggregate.value, "cameras": cameras}


@router.post("/extrinsic/preview/transcode")
async def extrinsic_preview_retry(request: Request) -> dict[str, object]:
    """Explicitly relaunch failed sweep transcodes (webapp Retry button)."""
    manager = get_manager(request)
    jobs = get_preview_jobs(request)
    directory = manager.extrinsic_dir()
    for camera in manager.current().cameras:
        source = directory / f"{camera.name}.mkv"
        if (await jobs.status(source)).state is PreviewState.FAILED:
            jobs.retry(source)
    return await extrinsic_preview_status(request)


@router.get("/extrinsic/{camera}/preview")
async def extrinsic_preview(request: Request, camera: str) -> FileResponse:
    """One camera's CFR-retimed H.264 preview of the sweep (ADR-0027)."""
    path = preview_path(get_manager(request).extrinsic_dir() / f"{camera}.mkv")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no preview for {camera}")
    return FileResponse(path, media_type="video/mp4")


_EXPORT_TARGET_IDS = {"caliscope", *PLATFORM_FORMATS}


class ExportRequest(BaseModel):
    """Targets to export (ADR-0026): all optional, none forced. 'caliscope' writes
    camera_array.toml; platform ids (threejs/blender/unity/unreal) write a JSON
    each. ``units`` applies to the platform JSONs only (the TOML stays mm)."""

    formats: list[str] = []
    # None = the session's persisted preference (seeded from TUNING at creation).
    units: Literal["mm", "m"] | None = None


def _export_board(session: CalibrationSession) -> CalibrationBoard:
    """Validate the session is exportable and return its board (ADR-0026)."""
    board = session.effective_extrinsic_board()
    if board is None:
        raise HTTPException(status_code=422, detail="no board defined")
    if not session.cameras or any(c.rotation is None for c in session.cameras):
        raise HTTPException(status_code=422, detail="extrinsic calibration incomplete")
    return board


def _reject_unknown_formats(formats: list[str]) -> None:
    unknown = [f for f in formats if f not in _EXPORT_TARGET_IDS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown formats: {', '.join(unknown)}")


def _render_target(
    session: CalibrationSession, target_id: str, square: float, units: str
) -> tuple[str, str]:
    """Serialized content of one export target — no disk write (dry-run + write)."""
    if target_id == "caliscope":
        return "toml", rtoml.dumps(caliscope_document(session, square, units=units))
    variant = platform_variant(session, target_id, square, units=units)
    return "json", json.dumps(variant, indent=2)


@router.post("/export")
async def export_calibration(request: Request, body: ExportRequest) -> dict[str, object]:
    """Write the selected export targets to the session folder and list them."""
    manager = get_manager(request)
    session = manager.current()
    board = _export_board(session)
    _reject_unknown_formats(body.formats)
    if not body.formats:
        raise HTTPException(status_code=422, detail="select at least one artifact")

    directory = manager.export_dir()
    directory.mkdir(parents=True, exist_ok=True)
    square = board_unit_mm(board)  # square side (ChArUco) or marker side (ArUco)
    units = body.units if body.units is not None else session.export_units
    selected = set(body.formats)
    files: list[dict[str, object]] = []
    for target in export_targets():  # catalog order, stable output
        if target.id not in selected:
            continue
        _language, content = _render_target(session, target.id, square, units)
        (directory / target.filename).write_text(content)
        files.append({"name": target.filename, "convention": target.label})

    manager.set_export_config(units, body.formats)
    manager.mark_exported()
    logging.getLogger(__name__).info("exported: %s", ", ".join(str(f["name"]) for f in files))
    return {"files": files}


@router.get("/export/conventions")
async def export_conventions() -> dict[str, object]:
    """Catalog of selectable export targets — backend = single source (ADR-0026)."""
    return {"targets": [asdict(t) for t in export_targets()]}


class ExportPreviewRequest(BaseModel):
    """Dry-run preview: render the selected targets' content without writing."""

    formats: list[str] = []
    units: Literal["mm", "m"] | None = None  # None = session preference


@router.post("/export/preview")
async def export_preview(request: Request, body: ExportPreviewRequest) -> dict[str, object]:
    """Return the exact bytes each selected target would write, without touching disk."""
    session = get_manager(request).current()
    board = _export_board(session)
    _reject_unknown_formats(body.formats)
    square = board_unit_mm(board)
    units = body.units if body.units is not None else session.export_units
    selected = set(body.formats)
    files: list[dict[str, object]] = []
    for target in export_targets():
        if target.id not in selected:
            continue
        language, content = _render_target(session, target.id, square, units)
        files.append({"name": target.filename, "language": language, "content": content})
    return {"files": files}


class ExportConfigRequest(BaseModel):
    """Persist the export config (units + targets) so it survives a reopen.

    ``units`` is required: setting the session preference must be explicit.
    """

    formats: list[str] = []
    units: Literal["mm", "m"]


@router.post("/export/config", response_model=SessionOut)
async def update_export_config(request: Request, body: ExportConfigRequest) -> SessionOut:
    """Store the export config on the session (ADR-0026) without writing files."""
    _reject_unknown_formats(body.formats)
    manager = get_manager(request)
    session = manager.set_export_config(body.units, body.formats)
    return _session_out(session, manager)


@router.get("/export/archive")
async def export_archive(request: Request) -> Response:
    """Download the export folder as a single zip."""
    directory = get_manager(request).export_dir()
    entries = sorted(directory.glob("*")) if directory.is_dir() else []
    if not entries:
        raise HTTPException(status_code=404, detail="nothing exported yet")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            if entry.is_file():
                archive.write(entry, arcname=entry.name)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="calibration_export.zip"'},
    )


class CameraOrderRequest(BaseModel):
    """Persist a drag-reorder: device paths in the operator's chosen order."""

    device_paths: list[str]


@router.post("/cameras/order", response_model=SessionOut)
async def reorder_cameras(request: Request, body: CameraOrderRequest) -> SessionOut:
    """Permute camera indices (anchor = position 0) without rebuilding configs.

    Unlike /cameras/config this KEEPS calibrations (they belong to the physical
    device); only index + position-based name change. Republishes so the track
    names follow.
    """
    manager = get_manager(request)
    try:
        session = manager.reorder_cameras(body.device_paths)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    publish_service = get_publish_service(request)
    if publish_service is not None:
        await publish_service.refresh()
    return _session_out(session, manager)


@router.post("/board", response_model=SessionOut)
async def define_board(request: Request, body: BoardConfigRequest) -> SessionOut:
    # Fail early: calibrate_intrinsic is ChArUco-only, so accepting a single
    # ArUco marker here would only fail at compute time, after the whole sweep.
    if (
        body.target == "intrinsic"
        and body.board is not None
        and body.board.board_type != "charuco"
    ):
        raise HTTPException(
            status_code=422,
            detail="intrinsic calibration requires a ChArUco board "
            "(single ArUco markers are extrinsic-only)",
        )
    manager = get_manager(request)
    try:
        board = _to_board(body.board) if body.board is not None else None
        if board is not None:
            validate_board(board)
        session = manager.define_board(body.target, board)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session_out(session, manager)


@router.post("/board/preview")
async def preview_board(body: BoardIn) -> Response:
    try:
        png = render_board_png(_to_board(body))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(content=png, media_type="image/png")


@router.get("/board/{target}/image.png")
async def board_image(request: Request, target: Literal["intrinsic", "extrinsic"]) -> Response:
    session = get_manager(request).current()
    board = (
        session.intrinsic_board
        if target == "intrinsic"
        else session.effective_extrinsic_board()
    )
    if board is None:
        raise HTTPException(status_code=404, detail=f"no {target} board defined")
    return Response(content=render_board_png(board), media_type="image/png")
