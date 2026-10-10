"""Extrinsic timestamp sidecars of an imported sweep (ADR-0007, ADR-0035).

Imported from a Caliscope ``timestamps.csv`` when the archive carries one, else
synthesised on the inferred capture grids, frame index by frame index. Split from
``import_session`` (EXP-19).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from calibration_service.recording.extrinsic_recorder import parse_caliscope_timestamps
from calibration_service.recording.replay import VideoProperties, decoded_frame_count
from calibration_service.session.import_contract import (
    CAMERA_PREFIX,
    CAMERA_RE,
    ImportPlan,
    ImportValidationError,
)
from calibration_service.synchronization.caliscope_alignment import (
    greedy_sync_mapping,
    inferred_grids,
)

logger = logging.getLogger(__name__)

# Real recorders and their timestamp logs disagree by a few TRAILING frames (stop
# flush; CAP_PROP_FRAME_COUNT is itself an estimate for some codecs). Tolerate up
# to ~1 s of tail drift; beyond that the csv is considered misaligned (frame i of
# the video would no longer be row i of the csv).
_ROWS_FRAMES_TOLERANCE = 30
# Last-resort sidecar cadence when a degenerate alignment has NOTHING to
# synchronize AND the videos declare no usable rate: purely arbitrary (any
# positive period does), deliberately NOT tied to the camera default_fps — this
# is not a capture cadence, just a non-zero slot spacing.
_FALLBACK_FPS = 30.0


def _is_inferred_grid(parsed: dict[str, list[float]]) -> bool:
    """True when a timestamps.csv carries an INFERRED uniform grid, not capture times.

    Caliscope's ``inferred_timestamps.csv`` spreads each camera's frames evenly
    over the recording (one constant delta per camera, no gaps, start at 0).
    Real capture logs always jitter; a perfectly constant grid on 100+ frames is
    synthetic. Nearest-time pairing on such grids drifts by whole frames between
    cameras (measured 10.2 px vs 3.7 px extrinsic RMSE) — the import falls back
    to the Caliscope-parity alignment instead (caliscope never reads this file
    back either).
    """
    suspicious = 0
    for times in parsed.values():
        if len(times) < 100:
            continue  # too short to judge
        deltas = np.diff(np.asarray(times, np.float64))
        median = float(np.median(deltas))
        if median <= 0:
            continue
        spread = float(np.max(deltas) - np.min(deltas))
        if spread < 1e-4 * median:  # one constant delta across the whole video
            suspicious += 1
    return 0 < suspicious == len(parsed)


def _csv_camera_index(key: str) -> int:
    """Map a timestamps.csv ``cam_id`` ("0", tolerant of "cam_0") to a camera number."""
    match = CAMERA_RE.fullmatch(key)
    if match is not None:
        return int(match.group(1))
    if key.isdigit():
        return int(key)
    raise ImportValidationError(f"timestamps.csv has an invalid cam_id {key!r}")


def _caliscope_aligned_times(
    plan: ImportPlan,
    props: dict[int, VideoProperties],
    sweep_dir: Path | None,
) -> dict[int, list[float]]:
    """Sidecar times from the Caliscope-parity videos-only alignment.

    Frames sharing a sync slot get the SAME stamp (slot * period), so the
    downstream sidecar grouping reproduces the slots exactly (spread 0). Frames
    a slot skipped (stall-breaker, rare) get a half-period offset: never an
    exact match, so they only ever fill a blank as a nearest fallback.

    Frame counts come from a full decode when the videos are available
    (``decoded_frame_count``): metadata estimates run a few frames long on
    remuxes, which shifts the grids and desynchronizes the blank positions
    (measured: pairing agreement 80% -> the estimate was the sole cause).
    """
    if sweep_dir is not None:
        counts = {
            str(video.index): decoded_frame_count(sweep_dir / f"{CAMERA_PREFIX}_{video.index}.mkv")
            for video in plan.extrinsic
        }
    else:  # unit tests without files on disk
        counts = {str(video.index): props[video.index].frames for video in plan.extrinsic}
    fps = {str(video.index): max(props[video.index].fps, 1e-6) for video in plan.extrinsic}
    grids = inferred_grids(counts, fps)
    slots = greedy_sync_mapping(grids)
    if not slots:
        raise ImportValidationError("could not align the extrinsic videos (no frames)")
    duration = max(series[-1] for series in grids.values() if series)
    # Slot spacing spans the recording: N slots cover [0, duration] in N-1 steps.
    # Degenerate fallback (<= 1 slot or zero duration = nothing to synchronize):
    # any positive period works, so derive it from the videos' own probed rate
    # rather than assuming 30 fps — a hand-inspected sidecar then reads true.
    # `fps` floors unusable probes at 1e-6 (a 0-fps container), which would blow
    # the period up to ~1e6 s: only trust plausible rates, else _FALLBACK_FPS.
    plausible = [rate for rate in fps.values() if rate > 1.0]
    fallback_period = 1.0 / max(plausible, default=_FALLBACK_FPS)
    period = duration / (len(slots) - 1) if len(slots) > 1 and duration > 0 else fallback_period
    times = {
        video.index: [(-1.0) for _ in range(counts[str(video.index)])] for video in plan.extrinsic
    }
    for slot, members in enumerate(slots):
        for key, frame in members.items():
            index = int(key)
            if frame < len(times[index]):
                times[index][frame] = slot * period
    for series in times.values():  # stall-dropped / tail frames: keep monotone
        previous = -period
        for i, value in enumerate(series):
            if value < 0:
                series[i] = previous + period / 2.0
            previous = series[i]
    logger.info(
        "caliscope-parity alignment: %d slots for %s frames",
        len(slots),
        {f"cam_{k}": v for k, v in sorted(counts.items())},
    )
    return times


def _sidecar_times(
    plan: ImportPlan,
    props: dict[int, VideoProperties],
    sweep_dir: Path | None = None,
) -> dict[int, list[float]]:
    """Per-camera frame times (seconds) for the extrinsic sidecars (ADR-0007).

    With a REAL Caliscope ``timestamps.csv`` (jittery capture times): its rows
    drive the sync, validated against each video's frame count. Without one —
    or when the csv is an inferred uniform grid (synthetic, mis-pairs cameras) —
    the videos are aligned the way Caliscope itself does it from videos alone
    (``caliscope_alignment``): consecutive consumption with blank slots.
    """
    if plan.timestamps_csv is not None:
        parsed = parse_caliscope_timestamps(plan.timestamps_csv)
        if _is_inferred_grid(parsed):
            logger.warning(
                "timestamps.csv is an inferred uniform grid (not capture times); "
                "ignoring it and aligning the videos caliscope-style instead"
            )
            return _caliscope_aligned_times(plan, props, sweep_dir)
        by_index: dict[int, list[float]] = {}
        for key, times in parsed.items():
            index = _csv_camera_index(key)
            if index in by_index:
                raise ImportValidationError(f"timestamps.csv lists cam_{index} twice")
            by_index[index] = times
        expected = {video.index for video in plan.extrinsic}

        if set(by_index) != expected and len(by_index) == len(expected):
            # Renumbered files vs original csv ids (typically 1-based Caliscope
            # ports vs our 0-based contract): remap ONLY when the two sorted sets
            # differ by one constant offset — deterministic, no guessing. The
            # per-camera rows==frames check below still guards a wrong pairing.
            pairs = list(zip(sorted(by_index), sorted(expected), strict=True))
            offsets = {csv_id - video_id for csv_id, video_id in pairs}
            if len(offsets) == 1 and offsets != {0}:
                offset = offsets.pop()
                logger.info(
                    "timestamps.csv ids look %+d-shifted; mapping cam_id N -> cam_(N%+d)",
                    offset,
                    -offset,
                )
                by_index = {csv_id - offset: times for csv_id, times in by_index.items()}

        extra = sorted(set(by_index) - expected)
        if extra:
            # A Caliscope timestamps.csv may cover more cameras than the archive
            # ships videos for (subset export): rows without a video are irrelevant
            # to the sync — drop them rather than reject the import.
            logger.info(
                "timestamps.csv covers cameras with no extrinsic video (ignored): %s",
                ", ".join(f"cam_{i}" for i in extra),
            )
            for index in extra:
                del by_index[index]
        for video in plan.extrinsic:
            rows = by_index.get(video.index)
            if rows is None:
                raise ImportValidationError(
                    f"timestamps.csv has no rows for cam_{video.index} "
                    f"(csv ids: {sorted(set(by_index))}, videos: {sorted(expected)})"
                )
            frames = props[video.index].frames
            drift = len(rows) - frames
            if abs(drift) > _ROWS_FRAMES_TOLERANCE:
                raise ImportValidationError(
                    f"timestamps.csv has {len(rows)} rows for cam_{video.index} "
                    f"but its video has {frames} frames"
                )
            if drift > 0:
                # More stamps than frames: drop the tail stamps — a sidecar line
                # must never reference a frame past the end of the video.
                by_index[video.index] = rows[:frames]
            if drift != 0:
                # Fewer stamps than frames is harmless as-is: the unstamped tail
                # frames simply never join a synchronized group.
                logger.info(
                    "cam_%d: timestamps.csv rows (%d) vs frames (%d) — tail drift tolerated",
                    video.index,
                    len(rows),
                    frames,
                )
        return by_index

    return _caliscope_aligned_times(plan, props, sweep_dir)
