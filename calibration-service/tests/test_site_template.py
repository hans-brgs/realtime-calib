"""The site template and its external checks (ADR-0062)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from numpy.typing import NDArray
from pydantic import ValidationError

from calibration_service.app import create_app
from calibration_service.calibration import ExtrinsicResult
from calibration_service.export import WorldFrame
from calibration_service.export.checks import template_checks
from calibration_service.export.opencv import BASIS
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.models.session import CalibrationSession, CameraConfig, CameraStatus
from calibration_service.session.manager import SessionManager
from calibration_service.site_template import SiteTemplate

MARKER = CalibrationBoard(
    board_type=BoardType.ARUCO, dictionary="DICT_4X4_100", columns=2, rows=2, marker_id=8
)
UNIT_M = MARKER.marker_size_mm / 1000.0
POSED = WorldFrame("target", "centre of the marker of group 0, as framed", "y", 0)
# Four cameras in the export world (Y up, metres), each looking at the origin.
CENTRES_M = {
    "cam_0": np.array([2.0, 2.1, 2.0]),
    "cam_1": np.array([-2.0, 2.2, 2.0]),
    "cam_2": np.array([-2.0, 2.0, -2.0]),
    "cam_3": np.array([2.0, 2.15, -2.0]),
}


def _device(name: str) -> str:
    return f"/dev/v4l/by-path/usb-{name}"


def _looking_at_origin(centre: NDArray[np.float64]) -> tuple[list[float], list[float]]:
    """The solver-frame pose (Rodrigues, translation in marker units) of a camera at
    ``centre`` (export world) aiming at the origin: pitch asin(y/|c|), yaw 0."""
    forward = -centre / np.linalg.norm(centre)
    right = np.cross(forward, [0.0, 1.0, 0.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    exported = np.stack([right, down, forward])  # export world -> camera
    rotation = exported @ BASIS  # R' = R M^T, M symmetric orthogonal
    solver_centre = BASIS.T @ centre / UNIT_M
    return [float(v) for v in cv2.Rodrigues(rotation)[0].ravel()], [
        float(v) for v in -rotation @ solver_centre
    ]


def _rig(
    centres: dict[str, NDArray[np.float64]] = CENTRES_M,
) -> tuple[CalibrationSession, ExtrinsicResult]:
    poses = {n: _looking_at_origin(c) for n, c in centres.items()}
    result = ExtrinsicResult(
        cameras=list(centres),
        rotations={n: p[0] for n, p in poses.items()},
        translations={n: p[1] for n, p in poses.items()},
        per_camera_error={n: 0.2 for n in centres},
        error=0.2,
        pair_errors={},
        group_count=1,
        point_count=4,
    )
    cameras = [
        CameraConfig(
            index=i,
            name=n,
            prefix="cam",
            device_path=_device(n),
            device_node=f"/dev/video{i}",
            width=1920,
            height=1080,
            resize_factor=0.5,
            fps=30,
            status=CameraStatus.EXTRINSIC_DONE,
            matrix=[[800.0, 0.0, 480.0], [0.0, 800.0, 270.0], [0.0, 0.0, 1.0]],
            distortions=[0.0] * 5,
            calibration_error=0.2,
            grid_count=40,
            rotation=poses[n][0],
            translation=poses[n][1],
            extrinsic_error=0.2,
        )
        for i, n in enumerate(centres)
    ]
    return CalibrationSession(session_id="site", cameras=cameras), result


def _template(**overrides: Any) -> dict[str, Any]:
    cameras = []
    for port, (name, centre) in enumerate(CENTRES_M.items()):
        cameras.append(
            {
                "device_path": _device(name),
                "port": port,
                "position_m": {
                    "x": sorted([np.sign(centre[0]) * 1.0, np.sign(centre[0]) * 3.0]),
                    "y": [2.0, 2.24],
                    "z": sorted([np.sign(centre[2]) * 1.0, np.sign(centre[2]) * 3.0]),
                },
                "pitch_deg": [25.0, 45.0],
                "yaw_to_origin_deg_max": 12.0,
            }
        )
    names = list(CENTRES_M)
    distances = [
        {"a": _device(a), "b": _device(b), "m": float(np.linalg.norm(CENTRES_M[a] - CENTRES_M[b]))}
        for a, b in zip(names, names[1:] + names[:1], strict=True)
    ]
    document = {
        "name": "dev room",
        "resolution": [960, 540],
        "cameras": cameras,
        "distances_m": distances,
    }
    document.update(overrides)
    return document


def _checks(template: dict[str, Any], world: WorldFrame = POSED, **rig: Any) -> dict[str, Any]:
    session, result = _rig(**rig)
    checks = template_checks(session, result, MARKER, world, SiteTemplate.model_validate(template))
    return {c.id: c for c in checks}


def test_a_matching_site_passes_every_check() -> None:
    checks = _checks(_template())
    assert {i: c.status for i, c in checks.items()} == {
        "template_binding": "ok",
        "template_placement": "ok",
        "template_scale": "ok",
        "template_resolution": "ok",
    }
    assert all(c.scope == "external" for c in checks.values())
    assert checks["template_scale"].value == pytest.approx(0.0, abs=1e-9)


def test_binding_sees_a_swapped_cable_a_missing_and_an_extra_camera() -> None:
    template = _template()
    template["cameras"][0]["port"], template["cameras"][1]["port"] = 1, 0
    binding = _checks(template)["template_binding"]
    assert binding.status == "fail"
    assert "at port 0, expected 1" in binding.detail
    template = _template()
    template["cameras"][3]["device_path"] = "/dev/v4l/by-path/usb-elsewhere"
    template["distances_m"] = []
    binding = _checks(template)["template_binding"]
    assert binding.status == "fail" and "missing" in binding.detail
    template = _template()
    template["cameras"] = template["cameras"][:3]
    template["distances_m"] = template["distances_m"][:2]
    binding = _checks(template)["template_binding"]
    assert binding.status == "warn" and "cam_3" in binding.detail


def test_placement_reads_height_pitch_and_yaw_in_the_export_world() -> None:
    # cam_0 at 2.1 m and 2.83 m out: pitch atan(2.1 / 2.83) = 36.6 degrees.
    template = _template()
    template["cameras"][0]["pitch_deg"] = [25.0, 36.0]
    placement = _checks(template)["template_placement"]
    assert placement.status == "fail" and "cam_0 pitch 36.6°" in placement.detail
    template = _template()
    template["cameras"][1]["position_m"]["y"] = [2.0, 2.1]
    assert "cam_1 y 2.20 m" in _checks(template)["template_placement"].detail
    # A camera turned 20 degrees about the vertical, in place: its yaw to the origin.
    from calibration_service.export.checks import _placement

    _, result = _rig()
    rotation = cv2.Rodrigues(np.asarray(result.rotations["cam_2"]))[0]
    centre = -rotation.T @ np.asarray(result.translations["cam_2"])
    turned = rotation @ cv2.Rodrigues(np.array([0.0, np.radians(20.0), 0.0]))[0].T
    result.rotations["cam_2"] = [float(v) for v in cv2.Rodrigues(turned)[0].ravel()]
    result.translations["cam_2"] = [float(v) for v in -turned @ centre]
    assert _placement(result, MARKER)["cam_2"][2] == pytest.approx(20.0, abs=1e-6)


def test_placement_needs_a_posed_world() -> None:
    unposed = WorldFrame("anchor_camera", "optical centre of cam_0", None)
    assert _checks(_template(), world=unposed)["template_placement"].status == "unavailable"


def test_the_scale_is_caliscopes_weighted_least_squares() -> None:
    # The tape says 1 % longer: s = sum(m d / sigma^2) / sum(d^2 / sigma^2) = 1.01.
    template = _template()
    for distance in template["distances_m"]:
        distance["m"] *= 1.01
    scale = _checks(template)["template_scale"]
    assert scale.value == pytest.approx(0.01)
    assert scale.status == "fail"  # 1 % is many sigmas with 1 cm on 4 m distances
    # Its bands are 2 and 3 standard deviations of the implicit scale.
    solved = np.array(
        [
            np.linalg.norm(CENTRES_M[a] - CENTRES_M[b])
            for a, b in (
                ("cam_0", "cam_1"),
                ("cam_1", "cam_2"),
                ("cam_2", "cam_3"),
                ("cam_3", "cam_0"),
            )
        ]
    )
    sigma_scale = 0.01 / np.sqrt(np.sum(solved**2))
    assert scale.thresholds == pytest.approx([2 * sigma_scale, 3 * sigma_scale])


def test_one_wrong_tape_distance_is_named() -> None:
    template = _template()
    template["distances_m"][0]["m"] += 0.05
    scale = _checks(template)["template_scale"]
    assert scale.status in ("warn", "fail")
    assert "disagree" in scale.detail and "cam_0|cam_1" in scale.detail


def test_resolution_and_no_template() -> None:
    assert _checks(_template(resolution=[1920, 1080]))["template_resolution"].status == "fail"
    session, result = _rig()
    absent = template_checks(session, result, MARKER, POSED, None)
    assert [c.status for c in absent] == ["unavailable"] * 4


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"cameras": []}, "at least 1"),
        ({"surprise": 1}, "Extra inputs"),
        ({"cameras": [{"device_path": "a", "port": 0, "pitch_deg": [40, 30]}]}, "min, max"),
        ({"cameras": [{"device_path": "a", "port": 0}, {"device_path": "a", "port": 1}]}, "twice"),
        ({"cameras": [{"device_path": "a", "port": 0}, {"device_path": "b", "port": 0}]}, "twice"),
        (
            {
                "cameras": [{"device_path": "a", "port": 0}],
                "distances_m": [{"a": "a", "b": "c", "m": 1}],
            },
            "not in the template",
        ),
    ],
)
def test_inconsistent_templates_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SiteTemplate.model_validate({**_template(distances_m=[]), **change})


def test_the_routes_store_the_template_and_checks_carry_its_footprint(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    assert client.get("/settings/site-template").status_code == 404
    stored = client.put("/settings/site-template", json=_template())
    assert stored.status_code == 200
    digest = hashlib.sha256((tmp_path / "site_template.json").read_bytes()).hexdigest()
    assert stored.json()["sha256"] == digest
    assert client.get("/settings/site-template").json()["template"]["name"] == "dev room"
    charuco = {"board_type": "charuco", "dictionary": "DICT_5X5_100"}
    client.post("/board", json={"target": "intrinsic", "board": charuco})
    board = {"board_type": "aruco", "dictionary": "DICT_4X4_100"}
    client.post("/board", json={"target": "extrinsic", "board": board})
    session, result = _rig()
    manager.current().cameras.extend(session.cameras)
    manager.extrinsic_dir().mkdir(parents=True, exist_ok=True)
    (manager.extrinsic_dir() / "result.json").write_text(json.dumps(result.__dict__))
    payload = client.get("/export/checks").json()
    assert payload["site_template"] == {"name": "dev room", "sha256": digest}
    statuses = {c["id"]: c["status"] for c in payload["checks"]}
    assert statuses["template_binding"] == "ok" and statuses["template_scale"] == "ok"
    assert statuses["template_placement"] == "unavailable"  # this world is not framed
    bad = client.put("/settings/site-template", json=_template(cameras=[]))
    assert bad.status_code == 422
    assert client.delete("/settings/site-template").json() == {"deleted": True}
    assert client.get("/export/checks").json()["site_template"] is None


def test_the_scale_weighs_each_tape_by_its_sigma() -> None:
    # Distinct sigmas and implied scales: only the weighted least squares of
    # Caliscope v0.11.5's scaled() lands on this value; a plain mean of m/d does not.
    template = _template()
    factors, sigmas = (0.990, 0.994, 1.004, 0.998), (0.005, 0.02, 0.01, 0.04)
    for distance, factor, sigma in zip(template["distances_m"], factors, sigmas, strict=True):
        distance["m"] *= factor
        distance["sigma_m"] = sigma
    scale = _checks(template)["template_scale"]
    solved = np.array([d["m"] / f for d, f in zip(template["distances_m"], factors, strict=True)])
    taped = np.array([d["m"] for d in template["distances_m"]])
    sigma = np.array(sigmas)
    expected = float(np.sum(taped * solved / sigma**2) / np.sum(solved**2 / sigma**2))
    assert scale.value == pytest.approx(expected - 1.0, rel=1e-9)
    assert scale.value != pytest.approx(float(np.mean(taped / solved)) - 1.0, rel=1e-3)


def test_a_world_too_large_reads_a_negative_scale_and_fails() -> None:
    # The lot 4 case: the solve 1.7 % too large, so the tape needs s < 1.
    template = _template()
    for distance in template["distances_m"]:
        distance["m"] *= 0.983
    scale = _checks(template)["template_scale"]
    assert scale.value == pytest.approx(-0.017)
    assert scale.status == "fail"
    assert "-1.70 %" in scale.detail


def test_disagreeing_tapes_warn_even_when_their_scale_is_fine() -> None:
    # Two distances off by +-1 % cancel in the scale, but contradict each other.
    template = _template()
    for distance, factor in zip(template["distances_m"], (1.01, 0.99, 1.0, 1.0), strict=True):
        distance["m"] *= factor
    scale = _checks(template)["template_scale"]
    assert abs(scale.value) <= scale.thresholds[0]
    assert scale.status == "warn" and "disagree" in scale.detail


def test_a_yaw_off_the_origin_fails_the_placement() -> None:
    template = _template()
    template["cameras"][2]["yaw_to_origin_deg_max"] = 12.0
    session, result = _rig()
    rotation = cv2.Rodrigues(np.asarray(result.rotations["cam_2"]))[0]
    centre = -rotation.T @ np.asarray(result.translations["cam_2"])
    turned = rotation @ cv2.Rodrigues(np.array([0.0, np.radians(20.0), 0.0]))[0].T
    result.rotations["cam_2"] = [float(v) for v in cv2.Rodrigues(turned)[0].ravel()]
    result.translations["cam_2"] = [float(v) for v in -turned @ centre]
    checks = template_checks(session, result, MARKER, POSED, SiteTemplate.model_validate(template))
    placement = {c.id: c for c in checks}["template_placement"]
    assert placement.status == "fail" and "cam_2 yaw 20.0°" in placement.detail


def test_an_imported_session_is_matched_by_the_template_port() -> None:
    # No devices to bind, but placement and scale still read through the ports.
    session, result = _rig()
    for camera in session.cameras:
        camera.device_path = f"import:{camera.name}.mp4"
    template = _template()
    template["cameras"][0]["pitch_deg"] = [25.0, 36.0]
    checks = {
        c.id: c
        for c in template_checks(
            session, result, MARKER, POSED, SiteTemplate.model_validate(template)
        )
    }
    assert checks["template_binding"].status == "unavailable"
    assert (
        checks["template_placement"].status == "fail"
        and "cam_0 pitch" in checks["template_placement"].detail
    )
    assert checks["template_scale"].status == "ok" and "4 of 4" in checks["template_scale"].detail


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {
                "cameras": [{"device_path": "a", "port": 0}],
                "distances_m": [{"a": "a", "b": "a", "m": 1}],
            },
            "joins a to itself",
        ),
        ({"cameras": [{"device_path": "a", "port": 0, "pitch_deg": [float("nan"), 45]}]}, "finite"),
        ({"cameras": [{"device_path": "a", "port": 0, "pitch_deg": [100, 120]}]}, "-90, 90"),
        (
            {"cameras": [{"device_path": "a", "port": 3}, {"device_path": "b", "port": 3}]},
            "port 3 appears twice",
        ),
    ],
)
def test_more_inconsistent_templates_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        SiteTemplate.model_validate({**_template(distances_m=[]), **change})


def test_an_unreadable_template_is_not_an_absent_one(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "default")
    client = TestClient(create_app(manager))
    charuco = {"board_type": "charuco", "dictionary": "DICT_5X5_100"}
    client.post("/board", json={"target": "intrinsic", "board": charuco})
    board = {"board_type": "aruco", "dictionary": "DICT_4X4_100"}
    client.post("/board", json={"target": "extrinsic", "board": board})
    session, result = _rig()
    manager.current().cameras.extend(session.cameras)
    manager.extrinsic_dir().mkdir(parents=True, exist_ok=True)
    (manager.extrinsic_dir() / "result.json").write_text(json.dumps(result.__dict__))
    typo = _template()
    typo["cameras"][0]["pitch_degs"] = typo["cameras"][0].pop("pitch_deg")
    (tmp_path / "site_template.json").write_text(json.dumps(typo))
    got = client.get("/settings/site-template")
    assert got.status_code == 422 and "pitch_degs" in got.json()["detail"]
    payload = client.get("/export/checks").json()
    assert "pitch_degs" in payload["site_template"]["error"]
    templated = [c for c in payload["checks"] if c["id"].startswith("template_")]
    assert len(templated) == 4
    assert all(c["status"] == "unavailable" and "unreadable" in c["detail"] for c in templated)
