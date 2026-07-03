"""FrameSynchronizer + co-visibility graph (ADR-0007: window, quorum, anti-famine)."""

from __future__ import annotations

import pytest

from calibration_service.synchronization import CovisibilityGraph, FrameSynchronizer

WINDOW = 0.040  # 40 ms


def _sync(cameras: list[str], **kwargs: int) -> FrameSynchronizer[str]:
    return FrameSynchronizer(cameras, WINDOW, **kwargs)


def test_groups_heads_within_the_window() -> None:
    sync = _sync(["cam_0", "cam_1", "cam_2"])
    sync.add("cam_0", 10.000, "a")
    sync.add("cam_1", 10.010, "b")
    sync.add("cam_2", 10.020, "c")
    group = sync.try_emit()
    assert group is not None
    assert set(group.frames) == {"cam_0", "cam_1", "cam_2"}
    assert group.spread == pytest.approx(0.020)
    assert group.timestamp == pytest.approx(10.010)


def test_quorum_requires_two_cameras() -> None:
    sync = _sync(["cam_0", "cam_1"])
    sync.add("cam_0", 10.0, "a")
    assert sync.try_emit() is None  # single head, no quorum


def test_stale_head_is_dropped_not_grouped() -> None:
    # cam_1's head is a full frame late -> dropped (anti-famine), no emission
    # (only one in-window head left).
    sync = _sync(["cam_0", "cam_1"])
    sync.add("cam_0", 10.100, "fresh")
    sync.add("cam_1", 10.000, "stale")
    assert sync.try_emit() is None
    # cam_1's next frame lands in-window -> groups with cam_0's untouched head.
    sync.add("cam_1", 10.110, "fresh2")
    group = sync.try_emit()
    assert group is not None
    assert group.frames["cam_1"].payload == "fresh2"
    assert group.frames["cam_0"].payload == "fresh"


def test_partial_group_waits_for_stragglers_then_emits() -> None:
    # 3 live cameras; only 2 in window. Instant batching holds the emission until
    # the wait budget (wait_depth buffered frames) is exhausted, then emits >= 2.
    sync = _sync(["cam_0", "cam_1", "cam_2"], wait_depth=3)
    sync.add("cam_0", 10.000, "a")
    sync.add("cam_1", 10.010, "b")
    assert sync.try_emit() is None  # cam_2 may still land in this instant
    sync.add("cam_0", 10.033, "a2")
    sync.add("cam_1", 10.043, "b2")
    assert sync.try_emit() is None  # still waiting (buffers below wait_depth)
    sync.add("cam_0", 10.066, "a3")
    group = sync.try_emit()  # cam_0 buffered 3 frames -> cam_2 presumed dead
    assert group is not None
    assert set(group.frames) == {"cam_0", "cam_1"}


def test_dead_camera_excluded_via_active_count() -> None:
    # With the live count lowered to 2, a 2-camera group is complete: no waiting.
    sync = _sync(["cam_0", "cam_1", "cam_2"])
    sync.set_active_count(2)
    sync.add("cam_0", 10.000, "a")
    sync.add("cam_1", 10.010, "b")
    group = sync.try_emit()
    assert group is not None
    assert set(group.frames) == {"cam_0", "cam_1"}


def test_drain_emits_buffered_groups_in_order() -> None:
    # Offline mode (compute over sidecars): everything buffered, then drained.
    sync = _sync(["cam_0", "cam_1"])
    for i in range(3):
        base = 10.0 + i * 0.1
        sync.add("cam_0", base, f"a{i}")
        sync.add("cam_1", base + 0.005, f"b{i}")
    groups = sync.drain()
    assert len(groups) == 3
    assert [g.frames["cam_0"].payload for g in groups] == ["a0", "a1", "a2"]
    assert all(g.spread <= WINDOW for g in groups)


def test_covisibility_counts_pairs_only_when_both_see_the_board() -> None:
    graph = CovisibilityGraph(["cam_0", "cam_1", "cam_2"])
    graph.record({"cam_0": True, "cam_1": True, "cam_2": False})
    graph.record({"cam_0": True, "cam_1": False, "cam_2": True})
    graph.record({"cam_0": True, "cam_1": True, "cam_2": True})
    assert graph.synced_groups == 3
    assert graph.count("cam_0", "cam_1") == 2
    assert graph.count("cam_1", "cam_0") == 2  # order-insensitive
    assert graph.count("cam_0", "cam_2") == 2
    assert graph.count("cam_1", "cam_2") == 1
    assert graph.board_frames == {"cam_0": 3, "cam_1": 2, "cam_2": 2}
    assert graph.degree("cam_0") == 2
    assert graph.degree("cam_1", min_shared=2) == 1  # only cam_0 reaches 2 shared
