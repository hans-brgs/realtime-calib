"""Operator runtime settings, persisted next to the sessions root (ADR-0036).

Rig-level trade-offs (encode quality vs disk, preview fluidity vs CPU) — NOT
session state: they survive across sessions in ``<sessions_dir>/settings.toml``.
The value hierarchy is: TUNING (compiled defaults) -> settings.toml (operator
preferences, this module) -> explicit request fields. Changes apply live: the
capture loops re-read the current settings (publication pacer swaps on the next
frame; recording quality applies to the next recording).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import rtoml

from calibration_service.tuning import TUNING

logger = logging.getLogger(__name__)

_SETTINGS_FILE = "settings.toml"


@dataclass(frozen=True)
class RuntimeSettings:
    """Current operator preferences; defaults come from TUNING when unset."""

    # JPEG quality of recorded mkvs — the pixels every offline compute re-detects.
    record_quality: int = TUNING.record_quality
    # LiveKit publication rate; None = follow the camera fps (full fidelity).
    preview_fps: int | None = TUNING.preview_fps


class SettingsStore:
    """Owns the current settings and their single-file persistence."""

    def __init__(self, sessions_dir: Path) -> None:
        self._path = sessions_dir / _SETTINGS_FILE
        self._current = self._load()

    @property
    def current(self) -> RuntimeSettings:
        return self._current

    def replace(self, settings: RuntimeSettings) -> RuntimeSettings:
        """Persist and adopt a full new set of preferences (PUT semantics)."""
        self._current = settings
        payload: dict[str, object] = {"record_quality": settings.record_quality}
        # TOML has no null: an absent key means "follow the camera fps".
        if settings.preview_fps is not None:
            payload["preview_fps"] = settings.preview_fps
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(rtoml.dumps(payload), encoding="utf-8")
        logger.info("settings saved: %s", payload)
        return settings

    def _load(self) -> RuntimeSettings:
        if not self._path.is_file():
            return RuntimeSettings()
        try:
            data = rtoml.load(self._path)
        except Exception:
            logger.exception("unreadable %s; falling back to TUNING defaults", self._path)
            return RuntimeSettings()
        preview = data.get("preview_fps")
        return RuntimeSettings(
            record_quality=int(data.get("record_quality", TUNING.record_quality)),
            preview_fps=int(preview) if preview is not None else None,
        )
