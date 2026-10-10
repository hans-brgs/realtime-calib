"""The extrinsic solve is discarded when its inputs change, never silently (ADR-0048).

Poses are stored in board units and solved against each camera's K: another
target, or a recomputed K, leaves poses the export would turn into a wrong array.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from calibration_service.app import create_app
from calibration_service.calibration.intrinsic import IntrinsicResult
from calibration_service.models.session import CameraStatus, WizardStep
from calibration_service.recording import VideoRecorder
from calibration_service.session import manager as manager_module
from calibration_service.session.manager import SessionManager
from calibration_service.session.store import save_session
from calibration_service.transport import api as api_module

CHARUCO = {"board_type": "charuco", "dictionary": "DICT_5X5_100", "columns": 7, "rows": 8}
CHARUCO_EXTRINSIC = {**CHARUCO, "square_size_mm": 40.0}
MARKER = {
    "board_type": "aruco",
    "dictionary": "DICT_4X4_100",
    "marker_id": 8,
    "marker_size_mm": 297.0,
}
CAMERAS = {
    "prefix": "cam",
    "cameras": [
        {
            "index": i,
            "device_path": f"/dev/v4l/by-path/cam{i}",
            "device_node": f"/dev/video{i}",
            "width": 64,
            "height": 48,
            "fps": 30,
        }
        for i in range(2)
    ],
}


def _solved_client(
    tmp_path: Path, *, inherited: bool = True, extrinsic: dict[str, object] = CHARUCO_EXTRINSIC
) -> tuple[TestClient, SessionManager]:
    """Two cameras with intrinsics AND a solved array (poses + result files), at Export."""
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    client.post("/board", json={"target": "intrinsic", "board": CHARUCO})
    defined = client.post(
        "/board", json={"target": "extrinsic", "board": extrinsic, "inherited": inherited}
    )
    assert defined.status_code == 200, defined.text
    client.post("/cameras/config", json=CAMERAS)
    session = manager.current()
    for i, camera in enumerate(session.cameras):
        camera.matrix = [[100.0, 0.0, 32.0], [0.0, 100.0, 24.0], [0.0, 0.0, 1.0]]
        camera.distortions = [0.0] * 5
        camera.rotation = [0.0, 0.0, 0.0]
        camera.translation = [float(i), 0.0, 0.0]
        camera.extrinsic_error = 0.3
        camera.status = CameraStatus.EXTRINSIC_DONE
    session.step = WizardStep.EXPORT
    save_session(tmp_path, session)  # on disk too: a reload must find the solve
    directory = manager.extrinsic_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("result.json", "ba_inputs.json", "manifest.json"):
        (directory / name).write_text("{}")
    return client, manager


def _assert_discarded(manager: SessionManager) -> None:
    session = manager.current()
    assert all(c.rotation is None and c.translation is None for c in session.cameras)
    assert all(c.status is CameraStatus.INTRINSIC_DONE for c in session.cameras)
    assert all(c.matrix is not None for c in session.cameras)  # intrinsics untouched
    assert session.step is WizardStep.EXTRINSIC_CAPTURE  # back to where it is recomputed
    directory = manager.extrinsic_dir()
    assert not (directory / "result.json").exists()
    assert not (directory / "ba_inputs.json").exists()
    assert (directory / "manifest.json").exists()  # the recording is kept


def test_another_extrinsic_target_needs_confirmation(tmp_path: Path) -> None:
    client, manager = _solved_client(tmp_path, inherited=False)
    refused = client.post("/board", json={"target": "extrinsic", "board": MARKER})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "discards_extrinsic"
    assert manager.current().cameras[0].rotation is not None  # nothing touched yet

    confirmed = client.post(
        "/board", json={"target": "extrinsic", "board": MARKER, "discard_extrinsic": True}
    )
    assert confirmed.status_code == 200
    _assert_discarded(manager)


def test_a_new_measurement_of_the_same_target_keeps_the_solve(tmp_path: Path) -> None:
    # ADR-0020: the measurement is the scale; re-measuring rescales the export
    # legitimately, so the poses (in board units) stay.
    client, manager = _solved_client(tmp_path, inherited=False)
    remeasured = {**CHARUCO, "square_size_mm": 40.4}
    response = client.post("/board", json={"target": "extrinsic", "board": remeasured})
    assert response.status_code == 200
    assert manager.current().cameras[1].translation == [1.0, 0.0, 0.0]
    assert (manager.extrinsic_dir() / "result.json").exists()


def test_an_intrinsic_grid_change_reaches_an_inherited_extrinsic_target(tmp_path: Path) -> None:
    client, manager = _solved_client(tmp_path, inherited=True)
    other_grid = {**CHARUCO, "columns": 6}
    refused = client.post("/board", json={"target": "intrinsic", "board": other_grid})
    assert refused.status_code == 409
    response = client.post(
        "/board", json={"target": "intrinsic", "board": other_grid, "discard_extrinsic": True}
    )
    assert response.status_code == 200
    _assert_discarded(manager)


def test_an_intrinsic_grid_change_spares_a_separate_extrinsic_target(tmp_path: Path) -> None:
    client, manager = _solved_client(tmp_path, inherited=False)
    other_grid = {**CHARUCO, "columns": 6}
    response = client.post("/board", json={"target": "intrinsic", "board": other_grid})
    assert response.status_code == 200
    assert manager.current().cameras[0].rotation is not None


def test_recomputing_intrinsics_needs_confirmation_and_discards_the_solve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, manager = _solved_client(tmp_path)
    with VideoRecorder(manager.intrinsic_video_path("cam_0"), 64, 48, fps=30) as recorder:
        recorder.write(np.zeros((48, 64, 3), np.uint8))
    result = IntrinsicResult(
        matrix=[[101.0, 0.0, 32.0], [0.0, 101.0, 24.0], [0.0, 0.0, 1.0]],
        distortions=[0.0] * 5,
        error=0.2,
        view_count=6,
        image_size=(64, 48),
    )
    monkeypatch.setattr(api_module, "compute_intrinsic_from_video", lambda *a, **k: result)

    refused = client.post("/intrinsic/cam_0/compute")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "discards_extrinsic"
    camera = manager.current().cameras[0]
    assert camera.matrix is not None and camera.matrix[0][0] == 100.0  # not recomputed

    confirmed = client.post("/intrinsic/cam_0/compute", json={"discard_extrinsic": True})
    assert confirmed.status_code == 200
    camera = manager.current().cameras[0]
    assert camera.matrix is not None and camera.matrix[0][0] == 101.0
    _assert_discarded(manager)


def test_rebuilding_the_cameras_forgets_the_sweep(tmp_path: Path) -> None:
    # Its videos are keyed by name: after a rebuild cam_0.mkv may show another
    # device than the new cam_0, so it is never offered for a recompute.
    client, manager = _solved_client(tmp_path)
    assert client.post("/cameras/config", json=CAMERAS).status_code == 200
    assert client.get("/session").json()["extrinsic_recorded"] is False
    for camera in manager.current().cameras:  # intrinsics redone since
        camera.matrix = [[100.0, 0.0, 32.0], [0.0, 100.0, 24.0], [0.0, 0.0, 1.0]]
        camera.distortions = [0.0] * 5
    response = client.post("/extrinsic/compute")
    assert response.status_code == 404, response.text


def test_rebuilding_the_cameras_drops_the_stale_solve_files(tmp_path: Path) -> None:
    # The fresh configs carry no pose; Minimize / orient reading the old
    # result.json would write poses of cameras that no longer exist.
    client, manager = _solved_client(tmp_path)
    assert client.post("/cameras/config", json=CAMERAS).status_code == 200
    assert not (manager.extrinsic_dir() / "result.json").exists()
    assert not (manager.extrinsic_dir() / "ba_inputs.json").exists()


# The rule (ADR-0048): the target's identity is every geometry field its type
# uses; only the measured *_mm sizes may change under a solve.
@pytest.mark.parametrize(
    ("solved_on", "edited"),
    [
        pytest.param(CHARUCO_EXTRINSIC, {"dictionary": "DICT_6X6_100"}, id="charuco-dictionary"),
        pytest.param(CHARUCO_EXTRINSIC, {"columns": 6}, id="charuco-columns"),
        pytest.param(CHARUCO_EXTRINSIC, {"rows": 7}, id="charuco-rows"),
        pytest.param(CHARUCO_EXTRINSIC, {"marker_ratio": 0.6}, id="charuco-marker-ratio"),
        pytest.param(CHARUCO_EXTRINSIC, {"inverted": True}, id="charuco-inverted"),
        pytest.param(CHARUCO_EXTRINSIC, {"legacy_pattern": True}, id="charuco-legacy-pattern"),
        pytest.param(MARKER, {"dictionary": "DICT_5X5_100"}, id="marker-dictionary"),
        pytest.param(MARKER, {"marker_id": 9}, id="marker-id"),
        pytest.param(MARKER, {"inverted": True}, id="marker-inverted"),
    ],
)
def test_every_geometry_field_of_the_target_needs_confirmation(
    tmp_path: Path, solved_on: dict[str, object], edited: dict[str, object]
) -> None:
    client, manager = _solved_client(tmp_path, inherited=False, extrinsic=solved_on)
    response = client.post("/board", json={"target": "extrinsic", "board": {**solved_on, **edited}})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "discards_extrinsic"
    assert manager.current().cameras[1].translation == [1.0, 0.0, 0.0]


@pytest.mark.parametrize(
    ("solved_on", "edited"),
    [
        pytest.param(CHARUCO_EXTRINSIC, {"square_size_mm": 40.4}, id="charuco-square-size"),
        pytest.param(CHARUCO_EXTRINSIC, {"marker_size_mm": 29.0}, id="charuco-marker-size"),
        pytest.param(MARKER, {"marker_size_mm": 300.0}, id="marker-size"),
        # A single marker ignores the grid fields and the marker/square ratio.
        pytest.param(MARKER, {"columns": 9, "rows": 4}, id="marker-ignored-grid"),
        pytest.param(MARKER, {"marker_ratio": 0.5}, id="marker-ignored-ratio"),
    ],
)
def test_a_measurement_or_an_ignored_field_keeps_the_solve(
    tmp_path: Path, solved_on: dict[str, object], edited: dict[str, object]
) -> None:
    client, manager = _solved_client(tmp_path, inherited=False, extrinsic=solved_on)
    response = client.post("/board", json={"target": "extrinsic", "board": {**solved_on, **edited}})
    assert response.status_code == 200
    assert manager.current().cameras[1].translation == [1.0, 0.0, 0.0]
    assert (manager.extrinsic_dir() / "result.json").exists()


@pytest.mark.parametrize("inherited_before", [True, False])
def test_switching_inheritance_on_the_same_geometry_keeps_the_solve(
    tmp_path: Path, inherited_before: bool
) -> None:
    client, manager = _solved_client(tmp_path, inherited=inherited_before)
    response = client.post(
        "/board",
        json={"target": "extrinsic", "board": CHARUCO_EXTRINSIC, "inherited": not inherited_before},
    )
    assert response.status_code == 200
    assert manager.current().cameras[1].translation == [1.0, 0.0, 0.0]


def test_an_invalid_definition_is_refused_before_the_discard_question(tmp_path: Path) -> None:
    # Confirming a discard for a request that then fails would ask for nothing:
    # a marker cannot inherit the ChArUco intrinsic board -> 422, never 409
    # (the inherited copy would be a ChArUco, another target than the marker).
    client, manager = _solved_client(tmp_path, inherited=False, extrinsic=MARKER)
    response = client.post(
        "/board", json={"target": "extrinsic", "board": MARKER, "inherited": True}
    )
    assert response.status_code == 422
    assert manager.current().cameras[1].translation == [1.0, 0.0, 0.0]


def test_a_compute_without_recording_is_a_404_before_the_discard_question(
    tmp_path: Path,
) -> None:
    client, _ = _solved_client(tmp_path)
    assert client.post("/intrinsic/cam_0/compute").status_code == 404


def test_the_discard_reaches_disk_before_the_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A crash between the two writes must not leave the new target next to
    # poses solved on the old one (the silent EXP-3 export after a restart).
    client, _ = _solved_client(tmp_path, inherited=False)

    def crash(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(manager_module, "save_board_config", crash)
    with pytest.raises(OSError):
        client.post(
            "/board", json={"target": "extrinsic", "board": MARKER, "discard_extrinsic": True}
        )
    reloaded = SessionManager(tmp_path, "default").current()
    assert all(camera.rotation is None for camera in reloaded.cameras)
    assert reloaded.extrinsic_board is not None
    assert reloaded.extrinsic_board.board_type == "charuco"  # the old target, without poses


def test_the_session_tells_whether_a_sweep_is_recorded(tmp_path: Path) -> None:
    # After a discard the Extrinsic step offers a recompute from this sweep.
    client, manager = _solved_client(tmp_path)
    assert client.get("/session").json()["extrinsic_recorded"] is True
    (manager.extrinsic_dir() / "manifest.json").unlink()
    assert client.get("/session").json()["extrinsic_recorded"] is False
