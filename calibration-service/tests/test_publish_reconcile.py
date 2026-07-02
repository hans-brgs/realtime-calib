"""On-demand capture reconciliation: the view -> live-camera-set mapping (ADR-0021)."""

from __future__ import annotations

from pathlib import Path

from calibration_service.config import LiveKitConfig
from calibration_service.session.manager import SessionManager
from calibration_service.transport.camera_publish_service import (
    CameraPublishService,
    _PublishTarget,
)


def _service(tmp_path: Path) -> CameraPublishService:
    return CameraPublishService(LiveKitConfig(), SessionManager(tmp_path))


def _targets(*names: str) -> dict[str, _PublishTarget]:
    return {
        name: _PublishTarget(name, i, f"/dev/video{i}", 1920, 1080, 30)
        for i, name in enumerate(names)
    }


def test_unreported_view_publishes_all(tmp_path: Path) -> None:
    service = _service(tmp_path)  # _active_view is None until the webapp reports
    by_name = _targets("cam_0", "cam_1", "cam_2")
    assert service._desired_cameras(by_name) == {"cam_0", "cam_1", "cam_2"}


def test_camera_setup_and_extrinsic_views_publish_all(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    service.set_active_view("cameras")
    assert service._desired_cameras(by_name) == {"cam_0", "cam_1"}
    service.set_active_view("extrinsic")
    assert service._desired_cameras(by_name) == {"cam_0", "cam_1"}


def test_intrinsic_view_publishes_only_the_active_camera(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1", "cam_2")
    service.set_active_view("intrinsic")
    service.set_active_intrinsic("cam_1")
    assert service._desired_cameras(by_name) == {"cam_1"}


def test_intrinsic_view_without_active_publishes_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    service.set_active_view("intrinsic")
    service.set_active_intrinsic(None)
    assert service._desired_cameras(by_name) == set()


def test_intrinsic_active_absent_from_targets_publishes_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    service.set_active_view("intrinsic")
    service.set_active_intrinsic("cam_9")  # not among the configured cameras
    assert service._desired_cameras(by_name) == set()


def test_passive_views_publish_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    by_name = _targets("cam_0", "cam_1")
    for view in ("boards", "review", "export", "session", "load"):
        service.set_active_view(view)
        assert service._desired_cameras(by_name) == set(), view
