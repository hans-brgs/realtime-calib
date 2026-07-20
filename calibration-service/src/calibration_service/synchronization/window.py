"""Sync-window derivation shared by the live and offline paths (ADR-0037).

One rule for both worlds: the grouping tolerance is just below one frame
interval (never chain two consecutive frames of the same camera into one
group), clamped against degenerate cadences. The LIVE synchronizer feeds it
the slowest camera's configured period; the OFFLINE solve feeds it the max of
the median recorded inter-frame deltas (imported sessions have no capture
grid — there, the measurement stays the truth). Before ADR-0037 the two paths
used diverging factors (1.2 live vs 0.95 offline): the live gauges grouped
more generously than the solve ever would.
"""

from __future__ import annotations

# < 1 frame interval: two consecutive frames of one camera can never share a group.
_WINDOW_FACTOR = 0.95
# Degenerate-cadence guards (corrupt sidecar, exotic import): 20 ms - 250 ms.
_WINDOW_MIN_S = 0.02
_WINDOW_MAX_S = 0.25


def sync_window(period_s: float) -> float:
    """Grouping tolerance for a frame period: ``clip(0.95 x period, 20 ms, 250 ms)``."""
    return min(max(_WINDOW_FACTOR * period_s, _WINDOW_MIN_S), _WINDOW_MAX_S)
