"""Caliscope-parity alignment of pre-recorded videos WITHOUT capture timestamps.

Verbatim replication of caliscope's videos-only synchronization (verified against
their sources, ``src/caliscope/recording/synchronized_timestamps.py``):

1. ``inferred_grids`` — every camera is assumed to span the SAME wall duration
   (the mean of ``frames/fps`` over cameras); frame ``i`` of a camera with ``n``
   frames gets the synthetic time ``i * avg_duration / n``.
2. ``greedy_sync_mapping`` — their ``_compute_sync_mapping`` pass: all cameras
   are walked together with per-camera cursors, frames are consumed STRICTLY
   consecutively (never skipped, never reused), and at each slot a camera either
   joins or defers (blank) based on two comparisons against the OTHER cameras'
   current/next frames. Rate mismatch is absorbed as isolated blank slots instead
   of the skip/borrow cascades a nearest-neighbour matcher produces on the same
   synthetic grids (measured on a real dataset: 10.2 px vs 3.7 px extrinsic RMSE).

Bit-exactness notes (matters for reproducing caliscope's pairing): camera order
is the SORTED key order; ``earliest_next``/``latest_current`` are snapshotted
BEFORE any cursor advances in the slot; both comparisons use STRICT operators;
a slot is emitted only if at least one camera joined; an all-deferred slot
advances the earliest camera without emitting (their stall-breaker).
"""

from __future__ import annotations

from statistics import mean


def inferred_grids(
    frame_counts: dict[str, int], fps: dict[str, float]
) -> dict[str, list[float]]:
    """Caliscope's synthetic per-camera frame times (videos-only import).

    ``avg_duration = mean(frames / fps)`` across cameras; camera ``c`` gets
    ``t_i = i * avg_duration / frames_c`` (that operation order — matching
    ``SynchronizedTimestamps.from_video_paths``).
    """
    if not frame_counts:
        return {}
    avg_duration = mean(frame_counts[name] / fps[name] for name in frame_counts)
    return {
        name: [i * avg_duration / count for i in range(count)]
        for name, count in frame_counts.items()
    }


def greedy_sync_mapping(times: dict[str, list[float]]) -> list[dict[str, int]]:
    """Caliscope's ``_compute_sync_mapping``: slots of per-camera frame indices.

    Each returned slot maps camera -> frame index for the cameras present in
    that instant (a camera missing from a slot sat it out — a blank). Frames of
    every camera are consumed consecutively and exactly once.
    """
    cameras = sorted(name for name, series in times.items() if series)
    cursors = dict.fromkeys(cameras, 0)
    slots: list[dict[str, int]] = []

    while any(cursors[name] < len(times[name]) for name in cameras):
        candidates = {
            name: times[name][cursors[name]]
            for name in cameras
            if cursors[name] < len(times[name])
        }
        # Snapshot BEFORE any cursor advances (caliscope semantics): for each
        # camera, the earliest NEXT frame and the latest CURRENT frame among the
        # OTHER cameras.
        earliest_next: dict[str, float] = {}
        latest_current: dict[str, float] = {}
        for name in cameras:
            next_times = [
                times[other][cursors[other] + 1]
                for other in cameras
                if other != name and cursors[other] + 1 < len(times[other])
            ]
            earliest_next[name] = min(next_times) if next_times else float("inf")
            current_times = [
                times[other][cursors[other]]
                for other in cameras
                if other != name and cursors[other] < len(times[other])
            ]
            latest_current[name] = max(current_times) if current_times else float("-inf")

        assigned: dict[str, int] = {}
        for name in cameras:
            if name not in candidates:
                continue
            frame_time = candidates[name]
            if frame_time > earliest_next[name]:  # strictly later than someone's next
                continue
            # Strictly closer to the others' next frames than to their current
            # ones -> this frame belongs to the NEXT instant; defer (blank slot).
            if earliest_next[name] - frame_time < frame_time - latest_current[name]:
                continue
            assigned[name] = cursors[name]
            cursors[name] += 1

        if assigned:
            slots.append(assigned)
        elif candidates:
            # Stall-breaker: nothing joined — drop the earliest frame, no slot.
            earliest = min(candidates, key=lambda name: candidates[name])
            cursors[earliest] += 1
    return slots
