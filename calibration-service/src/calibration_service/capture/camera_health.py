"""Per-camera capture health and reopen backoff (spec realtime-telemetry, #46).

A camera that failed to open, or stopped delivering frames mid-capture, used to
die in the logs: the publisher retried it every reconcile tick (~1 s) forever,
with a full traceback each time, and the operator saw a missing or frozen tile
with no reason. This registry is the one place that state lives — the publisher
reports what happened, reads back whether a reopen is due, and broadcasts the
snapshot as the ``camera_state`` telemetry message.

Pure bookkeeping, no I/O and no clock of its own: every call takes ``now`` from
the caller's monotonic clock (like :class:`GridPacer`), so the backoff is tested
without sleeping.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

# Reopen backoff for a camera in error: 1 s, 2, 4, 8, 16, then 30 s for good. The
# first retry stays at the reconcile tick so a transient USB hiccup recovers as
# fast as before; the cap bounds both the log rate and how long a re-plugged
# camera waits before it is picked up again.
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 30.0


class CaptureState(StrEnum):
    """Wire values of ``camera_state.cameras[*].state``.

    Named apart from ``models.session.CameraStatus`` on purpose: that one is
    calibration progress (persisted), this one is live capture health (volatile).
    """

    LIVE = "live"  # open and delivering frames
    OPENING = "opening"  # open attempt in flight
    ERROR = "error"  # open failed or stream lost; reopen pending (backoff)
    IDLE = "idle"  # deliberately closed for the current view (ADR-0021)


# States sync() may reset to IDLE when the view stops wanting the camera: no device
# is held open in either, so there is nothing for the publisher to close first.
_PARKABLE = frozenset({CaptureState.ERROR, CaptureState.OPENING})


@dataclass
class _Entry:
    state: CaptureState
    since: float
    reason: str | None = None
    failures: int = 0  # consecutive failures since the last success / reset
    retry_at: float | None = None


def backoff_delay(failures: int) -> float:
    """Delay before the next reopen after ``failures`` consecutive failures (>= 1)."""
    return min(_BACKOFF_BASE_S * 2.0 ** (failures - 1), _BACKOFF_CAP_S)


class CameraHealth:
    """Live capture state of every configured camera, keyed by track name."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def sync(self, configured: Iterable[str], desired: set[str], now: float) -> None:
        """Align the registry with the current config and view.

        Cameras no longer configured are forgotten; new ones start IDLE. A camera
        that is configured but not wanted by the view goes IDLE and loses its
        backoff — an error there is moot, and when the view asks for it again it is
        retried at once rather than after a stale 30 s wait. LIVE cameras are left
        alone: the publisher closes them first and reports it via :meth:`closed`.
        """
        names = set(configured)
        for name in list(self._entries):
            if name not in names:
                del self._entries[name]
        for name in names:
            entry = self._entries.get(name)
            parked = name not in desired and entry is not None and entry.state in _PARKABLE
            if entry is None or parked:
                self._entries[name] = _Entry(CaptureState.IDLE, now)

    def may_open(self, name: str, now: float) -> bool:
        """False only while a camera in error waits out its backoff."""
        entry = self._entries.get(name)
        if entry is None or entry.retry_at is None:
            return True
        return now >= entry.retry_at

    def opening(self, name: str, now: float) -> None:
        """An open attempt starts. Keeps the failure count, so a retry that fails
        again backs off further."""
        entry = self._entry(name, now)
        entry.state = CaptureState.OPENING
        entry.since = now
        entry.reason = None
        entry.retry_at = None

    def opened(self, name: str, now: float) -> int:
        """The camera is live. Returns how many failures preceded it (0 = clean
        open), so the caller can log a recovery."""
        entry = self._entry(name, now)
        recovered_from = entry.failures
        self._entries[name] = _Entry(CaptureState.LIVE, now)
        return recovered_from

    def failed(self, name: str, reason: str, now: float) -> int:
        """Open failed or the stream was lost: schedule the next reopen.

        Returns the consecutive failure count (1 on the first), so the caller logs
        the full traceback once and a short line on the retries.
        """
        entry = self._entry(name, now)
        failures = entry.failures + 1
        self._entries[name] = _Entry(
            CaptureState.ERROR,
            now,
            reason=reason,
            failures=failures,
            retry_at=now + backoff_delay(failures),
        )
        return failures

    def closed(self, name: str, now: float) -> None:
        """Deliberately closed (view change, reconfig): IDLE, backoff cleared."""
        self._entries[name] = _Entry(CaptureState.IDLE, now)

    def state(self, name: str) -> CaptureState | None:
        entry = self._entries.get(name)
        return entry.state if entry is not None else None

    def snapshot(self, now: float) -> dict[str, dict[str, object]]:
        """Per-camera JSON-ready view: the ``cameras`` field of ``camera_state``.

        Durations are RELATIVE (``for_s``, ``retry_in_s``): the host's monotonic
        clock means nothing to a tablet, and neither side needs a shared epoch.
        """
        return {
            name: {
                "state": entry.state.value,
                "reason": entry.reason,
                "for_s": round(max(0.0, now - entry.since), 1),
                "retry_in_s": (
                    round(max(0.0, entry.retry_at - now), 1) if entry.retry_at is not None else None
                ),
            }
            for name, entry in sorted(self._entries.items())
        }

    def _entry(self, name: str, now: float) -> _Entry:
        # A transition can be reported for a camera sync() has not seen yet (the
        # publisher syncs once per tick); register it rather than drop the report.
        return self._entries.setdefault(name, _Entry(CaptureState.IDLE, now))
