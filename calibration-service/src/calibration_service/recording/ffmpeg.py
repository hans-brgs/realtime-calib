"""Thin ffmpeg/ffprobe helpers for the pre-recorded session import (ADR-0035).

Ingest runs off the event loop (executor, like the intrinsic/extrinsic compute),
so these are **synchronous** wrappers around ``ffmpeg`` / ``ffprobe`` — both
guaranteed in the container image (Dockerfile, ADR-0027). Uploaded videos are
normalised into the canonical session layout by a container **remux** (``-c copy``:
no re-encode, frames preserved bit-for-bit, ChArUco fidelity untouched, a variable
frame rate kept as is); a frame-for-frame MJPG re-encode is the fallback only when
the remuxed file is unreadable.
"""

from __future__ import annotations

import logging
import subprocess
from fractions import Fraction
from pathlib import Path

logger = logging.getLogger(__name__)

_FFMPEG = "ffmpeg"
_FFPROBE = "ffprobe"
# Quiet, overwrite, fail-fast — same posture as the preview transcode (ADR-0027).
# -nostdin: ffmpeg must never read the service's stdin (SYN-9).
_BASE_ARGS = ("-nostdin", "-hide_banner", "-loglevel", "error", "-y")
# Bounds of an ingest call (SYN-9): a stuck ffmpeg would hold the import, and the
# service's operation lock with it (ADR-0050), forever. A probe is quick; a remux
# or a re-encode scales with the media, and is expected well under 1x real time.
PROBE_TIMEOUT_S = 30.0
_MIN_TRANSCODE_TIMEOUT_S = 120.0
_TRANSCODE_TIMEOUT_PER_MEDIA_S = 4.0


def transcode_timeout(duration_s: float) -> float:
    """Time allowed to remux or re-encode a video lasting ``duration_s`` seconds."""
    return max(_MIN_TRANSCODE_TIMEOUT_S, _TRANSCODE_TIMEOUT_PER_MEDIA_S * duration_s)


class FfmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails or the binary is missing."""


def remux_copy_args(source: Path, destination: Path) -> list[str]:
    """ffmpeg args to repackage a video into another container WITHOUT re-encoding."""
    return [_FFMPEG, *_BASE_ARGS, "-i", str(source), "-c", "copy", str(destination)]


def reencode_args(source: Path, destination: Path, fps: float) -> list[str]:
    """ffmpeg args to re-encode a video to MJPG frame for frame (ADR-0054).

    Fallback when the remuxed file does not probe usable. Source frame i must stay
    frame i: the timestamp sidecars pair line i with decoded frame i (ADR-0035). A
    constant-rate filter (``-vf fps=``) duplicates and drops frames to fill its grid
    (a 62-frame VFR source came out as 61 frames, 12 of them duplicates); a plain
    ``-fps_mode passthrough`` fails at random on a real VFR source (two frames on one
    tick of the encoder's 1/fps time base, refused as "Invalid pts"); and re-timing
    by index under ``-r`` duplicated a frame of an MKV source past ~46 fps. So every
    frame gets pts = its index on an exact 1/fps time base, passed through as is.
    ``fps`` is the source's declared rate; the capture times live in the sidecars.
    MJPG keeps the intra-frame, frame-exact property the recorder relies on
    (ADR-0019).
    """
    rate = Fraction(fps).limit_denominator(1001)  # 29.97 -> 30000/1001
    tick = f"{rate.denominator}/{rate.numerator}"
    return [
        _FFMPEG,
        *_BASE_ARGS,
        "-i",
        str(source),
        "-an",
        "-vf",
        f"settb={tick},setpts=N",
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        tick,
        "-c:v",
        "mjpeg",
        # Quasi-lossless on the ffmpeg 2-31 scale — the import-side mirror of the
        # recorder's TUNING.record_quality: these are the pixels every compute
        # re-detects on, so the re-encode must not cost corner fidelity.
        "-q:v",
        "3",
        str(destination),
    ]


def run_ffmpeg(args: list[str], *, timeout_s: float) -> None:
    """Run an ffmpeg/ffprobe command, raising ``FfmpegError`` on failure.

    Synchronous by design: ingest runs in an executor (off the event loop), like
    the intrinsic/extrinsic compute. Mirrors the error handling of the preview
    transcode's ``_run`` (recording/preview.py). Past ``timeout_s`` the process
    is killed and the call fails.
    """
    try:
        result = subprocess.run(
            args, capture_output=True, check=False, stdin=subprocess.DEVNULL, timeout=timeout_s
        )
    except FileNotFoundError as exc:
        raise FfmpegError(f"{args[0]} not found in the container image") from exc
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(f"{args[0]} did not finish within {timeout_s:.0f} s") from exc
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip() or (
            f"{args[0]} exited with {result.returncode}"
        )
        raise FfmpegError(message)
