"""Tests for the Caliscope-parity videos-only alignment (replicated algorithm)."""

from __future__ import annotations

from calibration_service.synchronization.caliscope_alignment import (
    greedy_sync_mapping,
    inferred_grids,
)


def test_inferred_grids_share_the_mean_duration() -> None:
    grids = inferred_grids({"a": 6, "b": 5}, {"a": 5.0, "b": 5.0})
    # avg duration = mean(6/5, 5/5) = 1.1 s; t_i = i * 1.1 / n
    assert grids["a"][0] == 0.0
    assert grids["a"][1] == 1.1 / 6
    assert grids["b"][1] == 1.1 / 5
    assert len(grids["a"]) == 6 and len(grids["b"]) == 5


def test_greedy_mapping_defers_with_a_blank_instead_of_skipping() -> None:
    # Hand-traced example: A has 6 frames, B has 5 over the same span. B must
    # sit exactly one slot out (a blank) and consume its frames consecutively —
    # a nearest-neighbour matcher would instead skip/borrow around the drift.
    times = {
        "a": [0.0, 20.0, 40.0, 60.0, 80.0, 100.0],
        "b": [0.0, 25.0, 50.0, 75.0, 100.0],
    }
    slots = greedy_sync_mapping(times)
    assert slots == [
        {"a": 0, "b": 0},
        {"a": 1, "b": 1},
        {"a": 2, "b": 2},
        {"a": 3},  # b@75 is strictly closer to a's NEXT (80) than current (60)
        {"a": 4, "b": 3},
        {"a": 5, "b": 4},
    ]


def test_greedy_mapping_identity_on_equal_grids() -> None:
    grids = inferred_grids({"a": 50, "b": 50}, {"a": 30.0, "b": 30.0})
    slots = greedy_sync_mapping(grids)
    assert len(slots) == 50
    assert all(slot == {"a": i, "b": i} for i, slot in enumerate(slots))


def test_greedy_mapping_consumes_every_frame_exactly_once() -> None:
    # Unequal counts (like a real drop-heavy camera): all frames used, no skips,
    # no reuse; the deficit shows up purely as blank slots.
    grids = inferred_grids({"a": 100, "b": 93, "c": 97}, dict.fromkeys("abc", 30.0))
    slots = greedy_sync_mapping(grids)
    for name, count in (("a", 100), ("b", 93), ("c", 97)):
        used = [slot[name] for slot in slots if name in slot]
        assert used == list(range(count))  # consecutive, complete, unique
    blanks_b = sum(1 for slot in slots if "b" not in slot)
    assert blanks_b == len(slots) - 93
