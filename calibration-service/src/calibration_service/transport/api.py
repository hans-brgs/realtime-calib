"""HTTP API: session rehydration + camera detection/configuration (Phase 1).

Mounted at the service root; Caddy strips the ``/api`` prefix (ADR-0014).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from calibration_service.board import SUPPORTED_DICTIONARIES, render_board_png, validate_board
from calibration_service.calibration import compute_intrinsic_from_video
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
    # Persist the coverage grid next to the recording so the Results heatmap survives a
    # reload/resume (ADR-0022); the grid is normalised, so native vs output res is moot.
    coverage_path = manager.intrinsic_coverage_path(camera)
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_path.write_text(json.dumps([list(row) for row in result.coverage]))
    return _session_out(session)


@router.get("/intrinsic/{camera}/coverage")
async def intrinsic_coverage(request: Request, camera: str) -> dict[str, list[list[float]]]:
    """Serve the persisted coverage heatmap grid for the Results view (ADR-0022)."""
    path = get_manager(request).intrinsic_coverage_path(camera)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no coverage for {camera}")
    return {"coverage": json.loads(path.read_text())}


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
