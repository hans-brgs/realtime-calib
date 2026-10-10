"""Group per-camera detections into synchronized frames (ADR-0007).

Software timestamp sync for free-running USB cameras: frames whose CLOCK_MONOTONIC
timestamps (V4L2 buffer stamps, ADR-0049) fall within a tolerance window (< 1/fps)
form a synchronized group, kept only when a quorum (>= 2) of cameras participates.
Semantics ported from samvision's ``FrameSynchronizer`` (the precedent ADR-0007
cites), minus the ring-slot ownership — payloads here are small detection records,
not zero-copy frame views, so plain deques suffice:

- **Heads-in-window matching** relative to the *newest* head timestamp.
- **Anti-famine invariant**: any head outside the window is dropped on every
  pass, even when the others synchronize — a lagging camera can never freeze
  the pipeline, and a stale head can never pair with a newer frame anyway.
- **Instant batching**: don't emit a partial group while other cameras may still
  land in the same instant; emit once every camera is in OR the wait budget
  (``wait_depth`` buffered frames) is exhausted — quorum >= 2 applies.

It serves the live capture only (incremental ``add`` + ``try_emit``), for the
co-visibility gauges; the compute groups the recorded sidecars offline
(``sweep_groups``).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Frames a present camera may accumulate before we stop waiting for the missing
# ones and emit a partial (>= quorum) group. Bounds the wait for a dead/lagging
# camera to ~wait_depth/fps seconds without freezing the pipeline (samvision
# SYNC_WAIT_DEPTH). Deliberate invariants (feedback-only synchronizer, ADR-0036).
_WAIT_DEPTH = 3
# Per-camera buffer depth; deque(maxlen) silently evicts the oldest on overflow
# (2 s of margin at the 15 Hz extrinsic detection grid).
_MAX_BUFFER = 30
_QUORUM = 2  # >= 2 views to constrain an extrinsic pair (ADR-0007)


@dataclass(frozen=True)
class SyncFrame[T]:
    """One camera's contribution to a synchronized group."""

    camera: str
    timestamp: float  # CLOCK_MONOTONIC capture time (ADR-0007, ADR-0049)
    payload: T


@dataclass(frozen=True)
class SyncGroup[T]:
    """Frames of >= 2 cameras captured within the tolerance window."""

    frames: dict[str, SyncFrame[T]]  # keyed by camera name
    timestamp: float  # mean of member timestamps
    spread: float  # max - min member timestamp (diagnostic)


class FrameSynchronizer[T]:
    """Pair per-camera timestamped payloads into synchronized groups (ADR-0007)."""

    def __init__(self, cameras: list[str], window_s: float) -> None:
        self._cameras = list(cameras)
        self._window_s = window_s
        self._wait_depth = _WAIT_DEPTH
        # deque(maxlen) silently evicts the oldest on overflow — fine here (no
        # slot ownership to release, unlike samvision's rings).
        self._buffers: dict[str, deque[SyncFrame[T]]] = {
            name: deque(maxlen=_MAX_BUFFER) for name in self._cameras
        }

    def add(self, camera: str, timestamp: float, payload: T) -> None:
        """Buffer one camera's timestamped payload (unknown cameras ignored)."""
        buffer = self._buffers.get(camera)
        if buffer is not None:
            buffer.append(SyncFrame(camera, timestamp, payload))

    def try_emit(self) -> SyncGroup[T] | None:
        """Return the next synchronized group, or ``None`` if not ready."""
        heads = [buffer[0] for buffer in self._buffers.values() if buffer]
        if len(heads) < _QUORUM:
            return None

        newest = max(frame.timestamp for frame in heads)
        in_window = [f for f in heads if newest - f.timestamp <= self._window_s]

        # Anti-famine: drop every stale head each pass (see module docstring).
        for frame in heads:
            if newest - frame.timestamp > self._window_s:
                self._buffers[frame.camera].popleft()

        if len(in_window) < _QUORUM:
            return None
        complete = len(in_window) == len(self._cameras)
        waited_enough = any(len(self._buffers[f.camera]) >= self._wait_depth for f in in_window)
        if not (complete or waited_enough):
            return None  # instant batching — let stragglers land first
        members = [self._buffers[f.camera].popleft() for f in in_window]
        timestamps = [m.timestamp for m in members]
        return SyncGroup(
            frames={m.camera: m for m in members},
            timestamp=sum(timestamps) / len(timestamps),
            spread=max(timestamps) - min(timestamps),
        )
