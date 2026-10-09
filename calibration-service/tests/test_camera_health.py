"""Per-camera capture health + reopen backoff (spec realtime-telemetry, #46)."""

from __future__ import annotations

import pytest

from calibration_service.capture.camera_health import (
    CameraHealth,
    CaptureState,
    backoff_delay,
)


def test_backoff_doubles_from_the_reconcile_tick_and_caps_at_30s() -> None:
    assert [backoff_delay(n) for n in range(1, 8)] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]


def test_new_cameras_start_idle_and_may_open() -> None:
    health = CameraHealth()
    health.sync(["cam_0", "cam_1"], desired={"cam_0", "cam_1"}, now=0.0)
    assert health.state("cam_0") is CaptureState.IDLE
    assert health.may_open("cam_0", 0.0)


def test_a_failed_open_waits_out_its_backoff_before_the_next_attempt() -> None:
    health = CameraHealth()
    health.sync(["cam_0"], desired={"cam_0"}, now=0.0)
    health.opening("cam_0", 0.0)
    assert health.failed("cam_0", "cannot open /dev/video4", 0.0) == 1
    assert not health.may_open("cam_0", 0.5)
    assert health.may_open("cam_0", 1.0)
    # The retry fails again: the count carries over through OPENING, so it backs
    # off further instead of hammering the device every tick.
    health.opening("cam_0", 1.0)
    assert health.failed("cam_0", "cannot open /dev/video4", 1.0) == 2
    assert not health.may_open("cam_0", 2.9)
    assert health.may_open("cam_0", 3.0)


def test_a_successful_open_reports_the_recovery_and_resets_the_backoff() -> None:
    health = CameraHealth()
    health.sync(["cam_0"], desired={"cam_0"}, now=0.0)
    for t in (0.0, 1.0, 3.0):
        health.opening("cam_0", t)
        health.failed("cam_0", "cannot open /dev/video4", t)
    health.opening("cam_0", 7.0)
    assert health.opened("cam_0", 7.0) == 3  # recovered after 3 failures
    assert health.state("cam_0") is CaptureState.LIVE
    # A later loss starts the backoff over from the first step.
    assert health.failed("cam_0", "no frame for 3 s", 20.0) == 1
    assert health.may_open("cam_0", 21.0)


def test_a_clean_open_reports_no_recovery() -> None:
    health = CameraHealth()
    health.opening("cam_0", 0.0)
    assert health.opened("cam_0", 0.0) == 0


def test_leaving_the_view_clears_an_error_so_it_is_retried_at_once_on_return() -> None:
    health = CameraHealth()
    health.sync(["cam_0", "cam_1"], desired={"cam_0", "cam_1"}, now=0.0)
    for t in range(5):  # deep in the backoff: next retry 16 s away
        health.opening("cam_1", float(t))
        health.failed("cam_1", "cannot open /dev/video6", float(t))
    health.sync(["cam_0", "cam_1"], desired={"cam_0"}, now=5.0)  # intrinsic view on cam_0
    assert health.state("cam_1") is CaptureState.IDLE
    health.sync(["cam_0", "cam_1"], desired={"cam_0", "cam_1"}, now=6.0)
    assert health.may_open("cam_1", 6.0)


def test_sync_leaves_a_live_camera_to_the_publisher() -> None:
    # The publisher closes a leaver and reports it; sync must not pre-empt that
    # and show IDLE while the device is still open.
    health = CameraHealth()
    health.opening("cam_0", 0.0)
    health.opened("cam_0", 0.0)
    health.sync(["cam_0"], desired=set(), now=1.0)
    assert health.state("cam_0") is CaptureState.LIVE
    health.closed("cam_0", 1.0)
    assert health.state("cam_0") is CaptureState.IDLE


def test_unconfigured_cameras_are_forgotten() -> None:
    health = CameraHealth()
    health.sync(["cam_0", "cam_1"], desired={"cam_0", "cam_1"}, now=0.0)
    health.sync(["cam_0"], desired={"cam_0"}, now=1.0)
    assert health.state("cam_1") is None
    assert list(health.snapshot(1.0)) == ["cam_0"]


def test_snapshot_matches_the_camera_state_wire_contract() -> None:
    health = CameraHealth()
    health.sync(["cam_1", "cam_0"], desired={"cam_0", "cam_1"}, now=10.0)
    health.opening("cam_0", 10.0)
    health.opened("cam_0", 10.0)
    health.opening("cam_1", 10.0)
    health.failed("cam_1", "cannot open /dev/video6", 10.0)

    snapshot = health.snapshot(12.34)

    assert list(snapshot) == ["cam_0", "cam_1"]  # stable order
    assert snapshot["cam_0"] == {"state": "live", "reason": None, "for_s": 2.3, "retry_in_s": None}
    assert snapshot["cam_1"] == {
        "state": "error",
        "reason": "cannot open /dev/video6",
        "for_s": 2.3,
        "retry_in_s": 0.0,  # 1 s backoff already elapsed: due on this tick
    }


@pytest.mark.parametrize("state", list(CaptureState))
def test_wire_values_are_the_spec_literals(state: CaptureState) -> None:
    assert state.value in {"live", "opening", "error", "idle"}
