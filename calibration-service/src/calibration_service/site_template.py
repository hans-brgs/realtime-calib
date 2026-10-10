"""The site template (ADR-0062): a site's cameras, their placement and tape distances.

A site setting, not a session's: ``<sessions dir>/site_template.json``, written once and
checked at every export. Cameras are named by their ``device_path`` (a port is a session
index, not an identity); bounds live in the export world (Y up, metres).

Unlike our export contracts, whose readers ignore unknown fields, the template is an
input written by hand: an unknown field is refused, or a typo would drop a bound
without a word.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from calibration_service.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

SITE_TEMPLATE_FILE = "site_template.json"


def _ordered(value: tuple[float, float]) -> tuple[float, float]:
    if value[0] > value[1]:
        raise ValueError("a range is [min, max]")
    return value


Range = Annotated[tuple[float, float], AfterValidator(_ordered)]


class UnreadableTemplateError(ValueError):
    """A stored template that no longer validates: present, but not usable."""


class PositionBounds(BaseModel):
    """Optical centre bounds per export-world axis, metres; an omitted axis is free."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    x: Range | None = None
    y: Range | None = None  # height above the floor
    z: Range | None = None


def _pitch(value: tuple[float, float]) -> tuple[float, float]:
    if not -90.0 <= value[0] <= value[1] <= 90.0:
        raise ValueError("a pitch range lies within [-90, 90] degrees")
    return value


class TemplateCamera(BaseModel):
    """One camera of the site, by its device, at its expected port, within its bounds."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    device_path: str = Field(min_length=1, max_length=512)
    port: int = Field(ge=0, le=63)  # the session index this camera is expected at
    position_m: PositionBounds = Field(default_factory=PositionBounds)
    pitch_deg: Annotated[Range, AfterValidator(_pitch)] | None = None  # below the horizontal
    yaw_to_origin_deg_max: float | None = Field(default=None, ge=0.0, le=180.0)


class TemplateDistance(BaseModel):
    """A tape-measured distance between two optical centres (Caliscope's CameraDistance)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    a: str = Field(min_length=1, max_length=512)  # device paths
    b: str = Field(min_length=1, max_length=512)
    m: float = Field(gt=0.0, le=100.0)
    sigma_m: float = Field(default=0.01, gt=0.0, le=1.0)  # Caliscope v0.11.5's default


class SiteTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=200)
    resolution: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]] | None = None
    cameras: list[TemplateCamera] = Field(min_length=1, max_length=32)
    distances_m: list[TemplateDistance] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def _consistent(self) -> SiteTemplate:
        devices = [c.device_path for c in self.cameras]
        twice = sorted({d for d in devices if devices.count(d) > 1})
        if twice:
            raise ValueError(f"device path {twice[0]} appears twice")
        ports = [c.port for c in self.cameras]
        doubled = sorted({p for p in ports if ports.count(p) > 1})
        if doubled:
            raise ValueError(f"port {doubled[0]} appears twice")
        for distance in self.distances_m:
            if distance.a == distance.b:
                raise ValueError(f"a distance joins {distance.a} to itself")
            missing = {distance.a, distance.b} - set(devices)
            if missing:
                raise ValueError(f"a distance names cameras not in the template: {sorted(missing)}")
        return self


def _path(sessions_dir: Path) -> Path:
    return sessions_dir / SITE_TEMPLATE_FILE


def load_site_template(sessions_dir: Path) -> tuple[SiteTemplate, str] | None:
    """The stored template and the SHA-256 of its file, None when there is none.

    Raises ``UnreadableTemplateError`` with the first reason when the file exists but
    does not validate (hand-edited, or written by another version) or cannot be read.
    """
    path = _path(sessions_dir)
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        template = SiteTemplate.model_validate_json(raw)
    except OSError as exc:
        raise UnreadableTemplateError(f"site_template.json cannot be read: {exc}") from exc
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        reason = f"{where}: {first['msg']}" if where else str(first["msg"])
        raise UnreadableTemplateError(f"site_template.json is unreadable: {reason}") from exc
    return template, hashlib.sha256(raw).hexdigest()


def save_site_template(sessions_dir: Path, template: SiteTemplate) -> str:
    """Persist the template, normalised; returns the SHA-256 of the file written (not of
    the file deposited)."""
    text = template.model_dump_json(indent=2)
    atomic_write_text(_path(sessions_dir), text)
    logger.info("site template saved: %s", template.name)
    return hashlib.sha256(text.encode()).hexdigest()


def delete_site_template(sessions_dir: Path) -> bool:
    """Remove the template; whether there was one."""
    path = _path(sessions_dir)
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed
