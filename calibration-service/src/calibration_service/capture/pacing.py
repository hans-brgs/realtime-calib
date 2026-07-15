"""Absolute-grid frame pacing on the shared monotonic clock (ADR-0037).

Time is cut into fixed cells of ``1/rate`` seconds, anchored at the clock
origin — a ruler nobody ever moves, identical for every camera reading the
same clock. A frame is KEPT when it is the first to land in a not-yet-served
cell; later frames of the same cell are dropped (undecoded, upstream).

Why cells instead of a ``last = now`` threshold: a threshold re-anchors on
every kept frame, so one late frame shifts every following selection (phase
drift) and each camera drifts on its own. Cells never move, so a hiccup costs
at most one dropped/blank cell and the selection re-locks on the exact same
absolute grid — shared across cameras, which is what keeps their kept frames
within one cell of each other.

No sleeping here: the capture loop keeps DRAINING the driver (blocking
``grab()`` at the driver's own rate — parked threads, ~free) so buffered
frames never rot and timestamps stay honest even when a driver ignores
``CAP_PROP_FPS``. OpenCV has no non-blocking grab, so this drain is the only
implementable safeguard; the pacer only decides which drained frames to keep.

Ready upgrade if rig validation shows a disappointing group spread: keep the
frame CLOSEST to each cell boundary instead of the first one in the cell
(needs a one-frame candidate buffer, decoding every frame, and one frame of
decision latency). It only pays when a driver delivers faster than the grid —
at nominal rates the inter-camera spread is the sensors' physical phase
offset, invariant under the selection criterion (the fine nearest-neighbour
matching already happens offline, on the recorded timestamps).
"""

from __future__ import annotations


class GridPacer:
    """One-frame-per-cell selection on an absolute time grid.

    Instantiate one per cadence (capture/recording, detection, publication) —
    all cells derive from the same clock, so pacers of equal rate tick on the
    same instants across cameras.
    """

    def __init__(self, rate_hz: float) -> None:
        if rate_hz <= 0:
            raise ValueError(f"rate must be positive, got {rate_hz}")
        self._period = 1.0 / rate_hz
        self._last_cell = -1

    @property
    def period(self) -> float:
        """Cell width in seconds (``1/rate``)."""
        return self._period

    def due(self, now: float) -> bool:
        """True once per grid cell: for the first frame at/after each tick.

        Marks the cell as served — call exactly once per candidate frame.
        ``now`` must come from the shared monotonic clock.
        """
        cell = int(now / self._period)
        if cell > self._last_cell:
            self._last_cell = cell
            return True
        return False
