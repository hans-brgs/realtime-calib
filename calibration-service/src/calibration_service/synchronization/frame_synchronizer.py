"""Group per-camera detections into synchronized frames (ADR-0007).

Software timestamp sync for free-running USB cameras: frames whose host-monotonic
timestamps fall within a tolerance window (< 1/fps) form a synchronized group,
kept only when a quorum (>= 2) of cameras participates. Semantics ported from
samvision's ``FrameSynchronizer`` (the precedent ADR-0007 cites), minus the
ring-slot ownership — payloads here are small detection records, not zero-copy
frame views, so plain deques suffice:

- **Heads-in-window matching** relative to the *newest* head timestamp.
- **Anti-famine invariant**: any head outside the window is dropped on every
  pass, even when the others synchronize — a lagging camera can never freeze
  the pipeline, and a stale head can never pair with a newer frame anyway.
- **Instant batching**: don't emit a partial group while live cameras may still
  land in the same instant; emit once the live set is complete OR the wait
  budget (``wait_depth`` buffered frames) is exhausted — quorum >= 2 applies.

The same class serves the live capture (incremental ``add`` + ``try_emit``) and
the offline compute over recorded sidecar timestamps (``add`` everything, then
``drain`` — flush mode, no waiting).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Frames a present camera may accumulate before we stop waiting for the missing
# ones and emit a partial (>= quorum) group. Bounds the wait for a dead/lagging
# camera to ~wait_depth/fps seconds without freezing the pipeline (samvision
# SYNC_WAIT_DEPTH).
_DEFAULT_WAIT_DEPTH = 3
_DEFAULT_MAX_BUFFER = 30
_QUORUM = 2  # >= 2 views to constrain an extrinsic pair (ADR-0007)


@dataclass(frozen=True)
class SyncFrame[T]:
    """One camera's contribution to a synchronized group."""

    camera: str
    timestamp: float  # host-monotonic capture time (ADR-0007)
    payload: T


@dataclass(frozen=True)
class SyncGroup[T]:
    """Frames of >= 2 cameras captured within the tolerance window."""

    frames: dict[str, SyncFrame[T]]  # keyed by camera name
    timestamp: float  # mean of member timestamps
    spread: float  # max - min member timestamp (diagnostic)


class FrameSynchronizer[T]:
    """Pair per-camera timestamped payloads into synchronized groups (ADR-0007)."""

    def __init__(
        self,
        cameras: list[str],
        window_s: float,
        *,
        wait_depth: int = _DEFAULT_WAIT_DEPTH,
        max_buffer: int = _DEFAULT_MAX_BUFFER,
    ) -> None:
        self._cameras = list(cameras)
        self._window_s = window_s
        self._wait_depth = wait_depth
        # deque(maxlen) silently evicts the oldest on overflow — fine here (no
        # slot ownership to release, unlike samvision's rings).
        self._buffers: dict[str, deque[SyncFrame[T]]] = {
            name: deque(maxlen=max_buffer) for name in self._cameras
        }
        # Live cameras expected in a *complete* group; a dead camera must not
        # add wait_depth frames of latency to every group (samvision pattern).
        self._active_count = len(self._cameras)

    def set_active_count(self, count: int) -> None:
        """Update how many live cameras a complete group should contain."""
        self._active_count = max(1, min(count, len(self._cameras)))

    def add(self, camera: str, timestamp: float, payload: T) -> None:
        """Buffer one camera's timestamped payload (unknown cameras ignored)."""
        buffer = self._buffers.get(camera)
        if buffer is not None:
            buffer.append(SyncFrame(camera, timestamp, payload))

    def try_emit(self, *, flush: bool = False) -> SyncGroup[T] | None:
        """Return the next synchronized group, or ``None`` if not ready.

        ``flush=True`` (offline drain) emits as soon as the quorum is met,
        without waiting for stragglers — all data is already buffered.
        """
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

        complete = len(in_window) >= self._active_count
        waited_enough = any(
            len(self._buffers[f.camera]) >= self._wait_depth for f in in_window
        )
        if not (flush or complete or waited_enough):
            return None  # instant batching: let stragglers land first

        members = [self._buffers[f.camera].popleft() for f in in_window]
        timestamps = [m.timestamp for m in members]
        return SyncGroup(
            frames={m.camera: m for m in members},
            timestamp=sum(timestamps) / len(timestamps),
            spread=max(timestamps) - min(timestamps),
        )

    def drain(self) -> list[SyncGroup[T]]:
        """Emit every remaining group (offline mode: everything is buffered)."""
        groups: list[SyncGroup[T]] = []
        while (group := self.try_emit(flush=True)) is not None:
            groups.append(group)
        return groups
