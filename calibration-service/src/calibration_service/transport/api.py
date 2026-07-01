"""HTTP API: session rehydration + camera detection/configuration (Phase 1).

Mounted at the service root; Caddy strips the ``/api`` prefix (ADR-0014).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from calibration_service.models.camera import CameraDevice
from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
)
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


class SessionOut(BaseModel):
    session_id: str
    step: str
    mode: str
    intrinsic_fps: int
    optimization_strategy: str
    cameras: list[CameraConfigOut]


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
            )
            for c in session.cameras
        ],
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
