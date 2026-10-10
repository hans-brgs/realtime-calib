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

from calibration_service.atomic_io import atomic_write_text
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
        """Persist and adopt a full new set of preferences (PUT semantics).

        Written first, adopted after: a failed write leaves the previous settings
        both on disk and in effect, never a live value the next start forgets.
        """
        payload: dict[str, object] = {"record_quality": settings.record_quality}
        # TOML has no null: an absent key means "follow the camera fps".
        if settings.preview_fps is not None:
            payload["preview_fps"] = settings.preview_fps
        atomic_write_text(self._path, rtoml.dumps(payload))
        self._current = settings
        logger.info("settings saved: %s", payload)
        return settings

    def _load(self) -> RuntimeSettings:
        """Read ``settings.toml``; any unusable content falls back to TUNING.

        These are rig preferences, not calibration data: a hand-edited typo
        (``record_quality = "high"``) must cost the operator a preference, not the
        service start — it used to raise out of ``create_app``.
        """
        if not self._path.is_file():
            return RuntimeSettings()
        try:
            data = rtoml.load(self._path)
            quality = int(data.get("record_quality", TUNING.record_quality))
            preview = data.get("preview_fps")
            preview_fps = int(preview) if preview is not None else None
            # The bounds PUT /settings enforces, so a file cannot carry what the API refuses.
            low, high = TUNING.record_quality_bounds
            if not low <= quality <= high:
                raise ValueError(f"record_quality {quality} outside [{low}, {high}]")
            fps_max = max(TUNING.fps_options)
            if preview_fps is not None and not 1 <= preview_fps <= fps_max:
                raise ValueError(f"preview_fps {preview_fps} outside [1, {fps_max}]")
            return RuntimeSettings(record_quality=quality, preview_fps=preview_fps)
        except Exception:
            logger.exception("unusable %s; falling back to TUNING defaults", self._path)
            return RuntimeSettings()
