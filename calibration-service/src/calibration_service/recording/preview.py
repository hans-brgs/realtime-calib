"""Background H.264 preview transcodes for the Prepare replay (ADR-0027).

Recordings are MJPG-in-MKV at a VARIABLE effective cadence; the preview mp4 is
re-timed CFR **by index** (frame ``i`` shown at ``i / PREVIEW_FPS``) so the
webapp's ``<video>`` maps ``index = round(currentTime * PREVIEW_FPS)`` exactly —
trim bounds, stride and group indices land on the same frames the compute reads
from the original mkv (which stays untouched, with its timestamp sidecars).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from calibration_service.recording.replay import frame_count

logger = logging.getLogger(__name__)

# Fixed preview rate: the index <-> time contract with the webapp (ADR-0027).
PREVIEW_FPS = 30

_FFMPEG = "ffmpeg"
# x264 ultrafast at preview width: ~3 s for a 25 s 1080p MJPG on this project's
# reference machine; -g 15 keeps seeks snappy (<= 14 frames to decode).
_FFMPEG_ARGS = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
)


def preview_path(source: Path) -> Path:
    """The mp4 sitting next to a recording (derived artifact, regenerable)."""
    return source.with_suffix(".preview.mp4")


def transcode_args(source: Path, destination: Path) -> list[str]:
    """The exact ffmpeg invocation (CFR re-timed by index, ADR-0027)."""
    filters = f"setpts=N/({PREVIEW_FPS}*TB),scale=960:-2,format=yuv420p"
    return [
        _FFMPEG,
        *_FFMPEG_ARGS,
        "-i",
        str(source),
        "-an",
        "-vf",
        filters,
        "-r",
        str(PREVIEW_FPS),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "26",
        "-g",
        "15",
        "-movflags",
        "+faststart",
        str(destination),
    ]


class PreviewState(StrEnum):
    MISSING = "missing"  # no source recording at all
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class PreviewStatus:
    state: PreviewState
    frames: int = 0
    error: str | None = None


class PreviewJobs:
    """Owns the background transcode tasks, keyed by destination path.

    ``status()`` AUTO-ENQUEUES when the mp4 is missing but the source exists —
    recordings made before this feature self-heal on first visit (no legacy
    frame-server path, ADR-0027). A FAILED job never re-enqueues silently:
    ``retry()`` is the explicit path (webapp Retry button).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}
        self._frames: dict[str, int] = {}

    def ensure(self, source: Path) -> None:
        """Enqueue a transcode unless one is running/done/failed for this source."""
        destination = preview_path(source)
        key = str(destination)
        if key in self._tasks or key in self._errors or destination.is_file():
            return
        if not source.is_file():
            return
        self._tasks[key] = asyncio.create_task(
            self._run(source, destination), name=f"preview-{source.stem}"
        )

    def retry(self, source: Path) -> None:
        """Explicitly relaunch after a failure (clears the error, re-enqueues)."""
        key = str(preview_path(source))
        self._errors.pop(key, None)
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        self.ensure(source)

    def invalidate(self, source: Path) -> None:
        """A recording is being overwritten: drop its job/state and stale mp4."""
        destination = preview_path(source)
        key = str(destination)
        self._errors.pop(key, None)
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        destination.unlink(missing_ok=True)

    def status(self, source: Path) -> PreviewStatus:
        """Current state for a recording; auto-enqueues a missing preview."""
        destination = preview_path(source)
        key = str(destination)
        if key in self._errors:
            return PreviewStatus(PreviewState.FAILED, error=self._errors[key])
        if destination.is_file():
            task = self._tasks.get(key)
            if task is None or task.done():
                return PreviewStatus(PreviewState.DONE, frames=self._source_frames(source))
        if not source.is_file():
            return PreviewStatus(PreviewState.MISSING)
        self.ensure(source)
        return PreviewStatus(PreviewState.RUNNING)

    def _source_frames(self, source: Path) -> int:
        key = str(preview_path(source))
        if key not in self._frames:
            self._frames[key] = frame_count(source)
        return self._frames[key]

    async def _run(self, source: Path, destination: Path) -> None:
        partial = destination.with_suffix(".part.mp4")
        args = transcode_args(source, partial)
        logger.info("transcoding preview: %s", source.name)
        try:
            process = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await process.communicate()
            if process.returncode != 0:
                message = (stderr or b"").decode(errors="replace").strip() or (
                    f"ffmpeg exited with {process.returncode}"
                )
                raise RuntimeError(message)
            partial.replace(destination)  # atomic: readers only ever see a full mp4
            logger.info("preview ready: %s", destination.name)
        except FileNotFoundError:
            self._errors[str(destination)] = "ffmpeg not found in the container image"
            logger.error("preview transcode failed: ffmpeg not installed")
        except asyncio.CancelledError:
            partial.unlink(missing_ok=True)
            raise
        except Exception as exc:
            partial.unlink(missing_ok=True)
            self._errors[str(destination)] = str(exc)[:500]
            logger.exception("preview transcode failed for %s", source.name)
