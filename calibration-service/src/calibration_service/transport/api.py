"""HTTP API: session rehydration + camera detection/configuration (Phase 1).

Mounted at the service root; Caddy strips the ``/api`` prefix (ADR-0014).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal

import numpy as np
import rtoml
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

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
    aniposelib_document,
    caliscope_document,
    platform_variant,
)
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.camera import CameraDevice
from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
)
from calibration_service.recording import frame_count, read_frame_jpeg
from calibration_service.session.manager import SessionManager
from calibration_service.transport.camera_publish_service import CameraPublishService

router = APIRouter()


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
    resize_factor: float = 1.0
    fps: int


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
    board_type: str
    dictionary: str
    columns: int = 8
    rows: int = 5
    marker_ratio: float = 0.75
    marker_id: int = 0
    square_size_mm: float = 40.0
    marker_size_mm: float = 30.0
    inverted: bool = False


class BoardConfigRequest(BaseModel):
    target: Literal["intrinsic", "extrinsic"]
    board: BoardIn


class BoardOut(BoardIn):
    pass


class SessionOut(BaseModel):
    session_id: str
    step: str
    mode: str
    intrinsic_fps: int
    optimization_strategy: str
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


def _session_out(session: CalibrationSession) -> SessionOut:
    return SessionOut(
        session_id=session.session_id,
        step=session.step,
        mode=session.mode,
        intrinsic_fps=session.intrinsic_fps,
        optimization_strategy=session.optimization_strategy,
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
    return _session_out(get_manager(request).current())


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


@router.post("/cameras/detect", response_model=list[DetectedCameraOut])
async def detect_cameras(request: Request) -> list[DetectedCameraOut]:
    return [_device_out(d) for d in get_manager(request).detect()]


@router.post("/cameras/config", response_model=SessionOut)
async def configure_cameras(request: Request, body: ConfigRequest) -> SessionOut:
    configs = [_to_camera_config(body.prefix, item) for item in body.cameras]
    session = get_manager(request).configure_cameras(configs)

    # Reactive republish: apply the new config to the live LiveKit tracks (option a).
    publish_service = get_publish_service(request)
    if publish_service is not None:
        await publish_service.refresh()

    return _session_out(session)


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


class CaptureViewRequest(BaseModel):
    view: str | None  # wizard view id (e.g. "intrinsic", "cameras"), or null


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
    try:
        await service.start_intrinsic_recording(camera)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"recording": camera}


@router.post("/intrinsic/{camera}/stop")
async def stop_intrinsic(request: Request, camera: str) -> dict[str, object]:
    """Finalise the recording; the video is ready to compute."""
    service = get_publish_service(request)
    frames = await service.stop_intrinsic_recording() if service is not None else 0
    return {"camera": camera, "frames": frames}


@router.get("/intrinsic/{camera}/frames")
async def intrinsic_frame_count(request: Request, camera: str) -> dict[str, int]:
    """Total frame count of the recorded sweep, for the Prepare scrubber (ADR-0022)."""
    path = get_manager(request).intrinsic_video_path(camera)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no recording for {camera}")
    total = await asyncio.get_running_loop().run_in_executor(None, frame_count, path)
    return {"total": total}


@router.get("/intrinsic/{camera}/frame/{index}")
async def intrinsic_frame(request: Request, camera: str, index: int) -> Response:
    """Serve frame ``index`` of the recorded sweep as JPEG (ADR-0022, frame-server)."""
    path = get_manager(request).intrinsic_video_path(camera)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no recording for {camera}")
    jpeg = await asyncio.get_running_loop().run_in_executor(None, read_frame_jpeg, path, index)
    if jpeg is None:
        raise HTTPException(status_code=404, detail=f"no frame {index} for {camera}")
    return Response(content=jpeg, media_type="image/jpeg")


class ComputeRequest(BaseModel):
    """Prepare-step knobs (ADR-0022); all optional — omitted fields use auto/defaults."""

    stride: int | None = None  # "process every N frames" (read decimation); None = auto
    cap: int | None = None  # keyframe cap (plafond); None = default
    frame_start: int = 0  # trim start (frame index)
    frame_end: int | None = None  # trim end (exclusive); None = end of recording


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
                cap=params.cap,
                stride=params.stride,
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
    }
    metrics_path = manager.intrinsic_metrics_path(camera)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics))
    return _session_out(session)


@router.get("/intrinsic/{camera}/metrics")
async def intrinsic_metrics(request: Request, camera: str) -> dict[str, object]:
    """Serve the persisted review metrics for the Results view (ADR-0022).

    ``{coverage: heatmap grid, image_coverage: 5x5 fraction, orientation_bins: /8,
    board_quads: per-keyframe 4x3 board outline in camera coords}``.
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
    """Finalise the synchronized sweep; per-camera videos + sidecars are on disk."""
    service = get_publish_service(request)
    counts = await service.stop_extrinsic_recording() if service is not None else {}
    return {"frames": counts}


class ExtrinsicComputeRequest(BaseModel):
    """Prepare-step knobs (ADR-0023); omitted fields use defaults."""

    stride: int | None = None  # process every Nth synchronized group
    max_spread_ms: float | None = None  # drop groups with a larger timestamp spread
    min_shared: int | None = None  # minimum shared board views per camera pair


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
    max_spread_s = params.max_spread_ms / 1000.0 if params.max_spread_ms else None

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
                stride=params.stride,
                max_spread_s=max_spread_s,
                min_shared=params.min_shared or 5,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = manager.set_extrinsic_result(result)
    (directory / "result.json").write_text(json.dumps(asdict(result)))
    # BA observations: lets Minimize refine later without redetecting the videos.
    (directory / "ba_inputs.json").write_text(json.dumps(asdict(ba_inputs)))
    return _session_out(session)


@router.get("/extrinsic/result")
async def extrinsic_result(request: Request) -> dict[str, object]:
    """Serve the persisted array solve (poses + errors) for the Result 3D view."""
    path = get_manager(request).extrinsic_dir() / "result.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no extrinsic result")
    payload: dict[str, object] = json.loads(path.read_text())
    return payload


class OrientRequest(BaseModel):
    """Reorient the solved world frame (spec 3d-extrinsic-review, mutating)."""

    op: Literal["set_origin", "rotate"]
    group: int | None = None  # set_origin: synchronized-group whose board becomes origin
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

    ``set_origin`` puts the world origin/axes on the board of one group;
    ``rotate`` turns the frame ±90° about an axis. Reprojection quality is
    invariant, so errors carry over; the updated result payload is returned.
    """
    manager = get_manager(request)
    result = _load_extrinsic_result(manager)
    if body.op == "set_origin":
        if body.group is None or not (0 <= body.group < len(result.board_quads)):
            raise HTTPException(status_code=422, detail="invalid group")
        quad = result.board_quads[body.group]
        if quad is None:
            raise HTTPException(status_code=422, detail="group has no board pose")
        transform = quad_origin_transform(quad)
    else:
        if body.axis is None or body.degrees is None:
            raise HTTPException(status_code=422, detail="rotate needs axis + degrees")
        transform = axis_rotation_transform(body.axis, body.degrees)
    reoriented = reorient_result(result, transform)
    _store_extrinsic_result(manager, reoriented)
    payload: dict[str, object] = asdict(reoriented)
    return payload


@router.post("/extrinsic/minimize")
async def minimize_extrinsic(request: Request) -> dict[str, object]:
    """Re-run the bundle adjustment from the current result (spec 'Minimize').

    Uses the persisted BA observations (no redetection) and holds the anchor at
    its current pose, preserving any operator reorientation.
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
    _store_extrinsic_result(manager, refined)
    payload: dict[str, object] = asdict(refined)
    return payload


@router.get("/extrinsic/groups")
async def extrinsic_groups(
    request: Request,
    max_spread_ms: float | None = None,
    stride: int | None = None,
) -> dict[str, object]:
    """Synchronized groups of the recorded sweep, for the Prepare scrubber (ADR-0023).

    Synchronizes the timestamp sidecars only (no video decoding); what this lists
    is exactly what the compute consumes under the same knobs. The window derives
    from the RECORDED cadence (sidecar median inter-frame delta), not the config fps.
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
    if stride is not None and stride > 1:
        groups = groups[::stride]
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


@router.get("/extrinsic/{camera}/frame/{index}")
async def extrinsic_frame(request: Request, camera: str, index: int) -> Response:
    """Serve frame ``index`` of a camera's extrinsic recording as JPEG (scrubber)."""
    path = get_manager(request).extrinsic_dir() / f"{camera}.mkv"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no extrinsic recording for {camera}")
    jpeg = await asyncio.get_running_loop().run_in_executor(None, read_frame_jpeg, path, index)
    if jpeg is None:
        raise HTTPException(status_code=404, detail=f"no frame {index} for {camera}")
    return Response(content=jpeg, media_type="image/jpeg")


class ExportRequest(BaseModel):
    """Artifacts to export. The canonical Caliscope TOML is ALWAYS written; the
    optional list adds 'aniposelib' and/or platform variants (threejs, blender,
    unity, unreal) — see spec calibration-export."""

    formats: list[str] = []


@router.post("/export")
async def export_calibration(request: Request, body: ExportRequest) -> dict[str, object]:
    """Write the calibration export folder and list the produced files."""
    manager = get_manager(request)
    session = manager.current()
    board = session.effective_extrinsic_board()
    if board is None:
        raise HTTPException(status_code=422, detail="no board defined")
    if not session.cameras or any(c.rotation is None for c in session.cameras):
        raise HTTPException(status_code=422, detail="extrinsic calibration incomplete")
    unknown = [f for f in body.formats if f not in PLATFORM_FORMATS and f != "aniposelib"]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown formats: {', '.join(unknown)}")

    result_path = manager.extrinsic_dir() / "result.json"
    overall_error: float | None = None
    if result_path.is_file():
        overall_error = json.loads(result_path.read_text()).get("error")

    directory = manager.export_dir()
    directory.mkdir(parents=True, exist_ok=True)
    square = board_unit_mm(board)  # square side (ChArUco) or marker side (ArUco)
    files: list[dict[str, object]] = []

    rtoml.dump(caliscope_document(session, square), directory / "camera_array.toml")
    files.append({"name": "camera_array.toml", "convention": "opencv (canonical)"})
    if "aniposelib" in body.formats:
        rtoml.dump(
            aniposelib_document(session, square, overall_error),
            directory / "camera_array_aniposelib.toml",
        )
        files.append({"name": "camera_array_aniposelib.toml", "convention": "opencv (canonical)"})
    for format_id in body.formats:
        if format_id not in PLATFORM_FORMATS:
            continue
        variant = platform_variant(session, format_id, square)
        name = f"camera_array_{format_id}.json"
        (directory / name).write_text(json.dumps(variant, indent=2))
        convention = variant["convention"]
        assert isinstance(convention, dict)
        label = f"{convention['label']} · {convention['platforms']}"
        files.append({"name": name, "convention": label})

    manager.mark_exported()
    logger_files = ", ".join(str(f["name"]) for f in files)
    logging.getLogger(__name__).info("exported: %s", logger_files)
    return {"files": files}


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


@router.post("/board", response_model=SessionOut)
async def define_board(request: Request, body: BoardConfigRequest) -> SessionOut:
    try:
        board = _to_board(body.board)
        validate_board(board)
        session = get_manager(request).define_board(body.target, board)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _session_out(session)


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
