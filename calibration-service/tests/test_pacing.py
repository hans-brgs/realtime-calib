"""Absolute-grid pacing + unified sync window (ADR-0037)."""

from __future__ import annotations

import pytest

from calibration_service.capture.pacing import GridPacer
from calibration_service.synchronization.window import sync_window

PERIOD = 1 / 30  # 33.33 ms cells


def test_keeps_the_first_frame_of_each_cell_only() -> None:
    pacer = GridPacer(30)
    assert pacer.due(0.100) is True  # cell 3: first frame -> kept
    assert pacer.due(0.110) is False  # still cell 3 -> dropped
    assert pacer.due(0.134) is True  # cell 4
    assert pacer.due(0.165) is False  # still cell 4 (0.165 / 0.0333 = 4.95)
    assert pacer.due(0.167) is True  # cell 5


def test_a_late_frame_never_shifts_the_grid() -> None:
    # A threshold (`last = now`) would re-anchor on the late frame and shift every
    # following selection; the absolute cells stay put.
    pacer = GridPacer(30)
    assert pacer.due(3 * PERIOD + 0.001) is True  # cell 3, on time
    assert pacer.due(4 * PERIOD + 0.030) is True  # cell 4, ~30 ms late but same cell
    # Next frame lands early in cell 5: with a threshold it would be < period
    # after the late frame and be dropped; on the grid it is simply cell 5.
    assert pacer.due(5 * PERIOD + 0.001) is True


def test_an_empty_cell_is_skipped_without_catchup() -> None:
    pacer = GridPacer(30)
    assert pacer.due(3 * PERIOD) is True
    # Nothing arrived during cell 4; the next frame serves cell 5 exactly once.
    assert pacer.due(5 * PERIOD + 0.001) is True
    assert pacer.due(5 * PERIOD + 0.002) is False  # no double-serving as catch-up


def test_equal_rate_pacers_share_the_same_cells() -> None:
    # Two cameras reading the same clock tick on the same absolute instants.
    a, b = GridPacer(30), GridPacer(30)
    for t in (0.100, 0.110, 0.134, 0.165, 0.167):
        assert a.due(t) == b.due(t)


def test_rejects_a_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        GridPacer(0)
    with pytest.raises(ValueError):
        GridPacer(-30)


def test_sync_window_sits_just_below_one_frame_interval() -> None:
    assert sync_window(1 / 30) == pytest.approx(0.95 / 30)
    assert sync_window(1 / 15) == pytest.approx(0.95 / 15)


def test_sync_window_clamps_degenerate_cadences() -> None:
    assert sync_window(0.001) == 0.02  # absurdly fast sidecar -> floor
    assert sync_window(10.0) == 0.25  # absurdly slow -> ceiling
