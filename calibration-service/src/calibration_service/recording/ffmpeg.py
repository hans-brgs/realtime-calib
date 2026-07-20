"""Thin ffmpeg/ffprobe helpers for the pre-recorded session import (ADR-0031).

Ingest runs off the event loop (executor, like the intrinsic/extrinsic compute),
so these are **synchronous** wrappers around ``ffmpeg`` / ``ffprobe`` — both
guaranteed in the container image (Dockerfile, ADR-0027). Uploaded videos are
normalised into the canonical session layout by a container **remux** (``-c copy``:
no re-encode, frames preserved bit-for-bit, ChArUco fidelity untouched); a CFR
re-encode is the fallback only for variable-frame-rate sources.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_FFMPEG = "ffmpeg"
_FFPROBE = "ffprobe"
# Quiet, overwrite, fail-fast — same posture as the preview transcode (ADR-0027).
_BASE_ARGS = ("-hide_banner", "-loglevel", "error", "-y")
# Base vs average cadence divergence above this fraction => treat the source as VFR.
_VFR_TOLERANCE = 0.01


class FfmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails or the binary is missing."""


def remux_copy_args(source: Path, destination: Path) -> list[str]:
    """ffmpeg args to repackage a video into another container WITHOUT re-encoding."""
    return [_FFMPEG, *_BASE_ARGS, "-i", str(source), "-c", "copy", str(destination)]


def reencode_cfr_args(source: Path, destination: Path, fps: float) -> list[str]:
    """ffmpeg args to normalise a (VFR) source to constant-frame-rate MJPG.

    Fallback when a remux would leave a non-uniform cadence: MJPG keeps the
    intra-frame, frame-exact property the recorder relies on (ADR-0019).
    """
    return [
        _FFMPEG,
        *_BASE_ARGS,
        "-i",
        str(source),
        "-an",
        "-vf",
        f"fps={fps:.6f}",
        "-c:v",
        "mjpeg",
        # Quasi-lossless on the ffmpeg 2-31 scale — the import-side mirror of the
        # recorder's TUNING.record_quality: these are the pixels every compute
        # re-detects on, so the CFR normalisation must not cost corner fidelity.
        "-q:v",
        "3",
        str(destination),
    ]


def run_ffmpeg(args: list[str]) -> None:
    """Run an ffmpeg/ffprobe command, raising ``FfmpegError`` on failure.

    Synchronous by design: ingest runs in an executor (off the event loop), like
    the intrinsic/extrinsic compute. Mirrors the error handling of the preview
    transcode's ``_run`` (recording/preview.py).
    """
    try:
        result = subprocess.run(args, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise FfmpegError(f"{args[0]} not found in the container image") from exc
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip() or (
            f"{args[0]} exited with {result.returncode}"
        )
        raise FfmpegError(message)


def is_vfr(source: Path) -> bool:
    """Best-effort variable-frame-rate detection via ffprobe.

    Compares the stream's ``r_frame_rate`` (base rate) with ``avg_frame_rate``:
    they match for CFR, diverge for VFR. Returns ``False`` when either is unknown
    (can't tell -> remux and rely on the frame-index preview contract, ADR-0027).
    """
    args = [
        _FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        str(source),
    ]
    try:
        result = subprocess.run(args, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise FfmpegError("ffprobe not found in the container image") from exc
    if result.returncode != 0:
        raise FfmpegError(result.stderr.decode(errors="replace").strip() or "ffprobe failed")

    data = json.loads(result.stdout.decode(errors="replace") or "{}")
    streams = data.get("streams") if isinstance(data, dict) else None
    if not isinstance(streams, list) or not streams or not isinstance(streams[0], dict):
        return False
    r_rate = _parse_rate(streams[0].get("r_frame_rate"))
    avg_rate = _parse_rate(streams[0].get("avg_frame_rate"))
    if r_rate is None or avg_rate is None or r_rate <= 0 or avg_rate <= 0:
        return False
    return abs(r_rate - avg_rate) / r_rate > _VFR_TOLERANCE


def _parse_rate(value: object) -> float | None:
    """Parse an ffprobe rational rate (e.g. ``"30000/1001"``) into fps, or ``None``."""
    if not isinstance(value, str) or "/" not in value:
        return None
    num_s, _, den_s = value.partition("/")
    try:
        num, den = float(num_s), float(den_s)
    except ValueError:
        return None
    if den == 0:
        return None
    return num / den
