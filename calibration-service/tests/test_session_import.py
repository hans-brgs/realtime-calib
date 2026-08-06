"""Tests for the pre-recorded session ZIP import (ADR-0035).

Pure contract checks (naming, planning, zip-slip) run everywhere; the ingest
round-trips normalise real videos, so they are gated on ffmpeg being installed
(same pattern as the preview transcode tests).
"""

from __future__ import annotations

import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

import numpy as np
import pytest

from calibration_service.models.session import CameraConfig, WizardStep
from calibration_service.recording.extrinsic_recorder import read_timestamps
from calibration_service.recording.replay import VideoProperties, video_properties
from calibration_service.recording.video_writer import VideoRecorder
from calibration_service.session.import_session import (
    ImportPlan,
    ImportValidationError,
    PlannedVideo,
    UnreadableArchiveError,
    _sidecar_times,
    ingest,
    parse_camera_index,
    plan_import,
)
from calibration_service.session.manager import SessionManager
from calibration_service.session.store import create_session, list_sessions, load_session

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _make_video(
    path: Path, frames: int, size: tuple[int, int] = (64, 48), fps: int = 30
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    recorder = VideoRecorder(path, size[0], size[1], fps)
    for i in range(frames):
        recorder.write(np.full((size[1], size[0], 3), i * 7 % 255, np.uint8))
    recorder.close()


def _write_tree(base: Path, tree: dict[str, int | str]) -> Path:
    """Materialise {relpath: frame count (video) | literal text} under ``base``."""
    for rel, value in tree.items():
        target = base / rel
        if isinstance(value, int):
            _make_video(target, value)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)
    return base


def _make_zip(
    tmp_path: Path, name: str, tree: dict[str, int | str], wrapper: str | None = None
) -> Path:
    stage = _write_tree(tmp_path / f"stage-{name}", tree)
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                rel = file.relative_to(stage)
                bundle.write(file, f"{wrapper}/{rel}" if wrapper else str(rel))
    return archive


# --- naming contract ------------------------------------------------------------


def test_parse_camera_index_accepts_zero_padding() -> None:
    assert parse_camera_index("cam_0") == 0
    assert parse_camera_index("cam_00") == 0
    assert parse_camera_index("cam_07") == 7
    assert parse_camera_index("cam_12") == 12


@pytest.mark.parametrize("stem", ["webcam1", "cam_", "cam_a", "CAM_0", "cam-0", "cam_0_b"])
def test_parse_camera_index_rejects_bad_names(stem: str) -> None:
    with pytest.raises(ImportValidationError):
        parse_camera_index(stem)


# --- plan_import (pure scan, no ffmpeg) -------------------------------------------


def test_plan_import_inventories_both_phases(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "intrinsics/cam_00.mkv": 1,
            "intrinsics/cam_01.mkv": 1,
            "extrinsics/cam_00.mkv": 1,
            "extrinsics/cam_01.mkv": 1,
        },
    )
    plan = plan_import(root)
    assert [v.index for v in plan.intrinsic] == [0, 1]
    assert [v.index for v in plan.extrinsic] == [0, 1]
    assert plan.timestamps_csv is None


def test_plan_import_strips_single_wrapper_folder(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, {"my-recorded-set/intrinsics/cam_0.mkv": 1})
    plan = plan_import(root)
    assert [v.index for v in plan.intrinsic] == [0]


def test_plan_import_finds_timestamps_csv_in_extrinsics(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "intrinsics/cam_0.mkv": 1,
            "extrinsics/cam_0.mkv": 1,
            "extrinsics/timestamps.csv": "cam_id,frame_time\n0,1.0\n",
        },
    )
    csv = plan_import(root).timestamps_csv
    assert csv is not None and csv.parent.name == "extrinsics"


def test_plan_import_finds_timestamps_csv_at_root(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "intrinsics/cam_0.mkv": 1,
            "extrinsics/cam_0.mkv": 1,
            "timestamps.csv": "cam_id,frame_time\n0,1.0\n",
        },
    )
    csv = plan_import(root).timestamps_csv
    assert csv is not None and csv.parent == root


def test_plan_import_accepts_singular_folder_names(tmp_path: Path) -> None:
    """A Caliscope-style folder (singular ``extrinsic``) drops in as-is."""
    root = _write_tree(tmp_path, {"intrinsic/cam_0.mkv": 1, "extrinsic/cam_0.mkv": 1})
    plan = plan_import(root)
    assert [v.index for v in plan.intrinsic] == [0]
    assert [v.index for v in plan.extrinsic] == [0]


def test_plan_import_rejects_archive_without_phase_folders(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, {"videos/cam_0.mkv": 1})
    with pytest.raises(ImportValidationError, match="no intrinsics/"):
        plan_import(root)


def test_plan_import_rejects_extrinsics_only(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, {"extrinsics/cam_0.mkv": 1})
    with pytest.raises(ImportValidationError, match="no intrinsics/"):
        plan_import(root)


def test_plan_import_rejects_unsupported_extension(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, {"intrinsics/cam_0.webm": "x"})
    with pytest.raises(ImportValidationError, match="unsupported file"):
        plan_import(root)


def test_plan_import_rejects_stray_file(tmp_path: Path) -> None:
    """A mistyped file must fail loudly, not become a silently missing camera."""
    root = _write_tree(tmp_path, {"intrinsics/cam_0.mkv": 1, "intrinsics/notes.txt": "x"})
    with pytest.raises(ImportValidationError, match=r"notes\.txt"):
        plan_import(root)


def test_plan_import_rejects_duplicate_camera_number(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, {"intrinsics/cam_0.mkv": 1, "intrinsics/cam_00.mp4": 1})
    with pytest.raises(ImportValidationError, match="duplicate camera cam_0"):
        plan_import(root)


def test_plan_import_rejects_camera_set_mismatch(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        {
            "intrinsics/cam_0.mkv": 1,
            "extrinsics/cam_0.mkv": 1,
            "extrinsics/cam_1.mkv": 1,
        },
    )
    with pytest.raises(ImportValidationError, match="same cameras"):
        plan_import(root)


# --- ingest guards (no ffmpeg needed) ----------------------------------------------


def test_ingest_rejects_zip_slip_entry(tmp_path: Path) -> None:
    archive = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../evil.txt", "boom")
        bundle.writestr("intrinsics/cam_0.mkv", "fake")
    with pytest.raises(ImportValidationError, match="unsafe path"):
        ingest(archive, "slip", tmp_path / "sessions")


def test_ingest_rejects_tar_escape(tmp_path: Path) -> None:
    # The stdlib 'data' filter (PEP 706) is the tar counterpart of the zip-slip guard.
    archive = tmp_path / "slip.tar"
    with tarfile.open(archive, "w") as bundle:
        payload = b"boom"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    with pytest.raises(ImportValidationError, match="unsafe path"):
        ingest(archive, "tarslip", tmp_path / "sessions")


def test_ingest_rejects_non_archive(tmp_path: Path) -> None:
    archive = tmp_path / "junk.bin"
    archive.write_bytes(b"neither zip nor tar")
    with pytest.raises(UnreadableArchiveError):
        ingest(archive, "junky", tmp_path / "sessions")


def test_ingest_rejects_invalid_session_name(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, "any", {"intrinsics/cam_0.mkv": 1})
    with pytest.raises(ValueError, match="session name"):
        ingest(archive, "../evil", tmp_path / "sessions")


def test_ingest_rejects_existing_session(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    create_session(sessions, "taken")
    archive = _make_zip(tmp_path, "any", {"intrinsics/cam_0.mkv": 1})
    with pytest.raises(FileExistsError):
        ingest(archive, "taken", sessions)


# --- ingest round-trips (real ffmpeg) ----------------------------------------------


@requires_ffmpeg
def test_ingest_materialises_canonical_session(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    archive = _make_zip(
        tmp_path,
        "happy",
        {
            "intrinsics/cam_00.mkv": 10,
            "intrinsics/cam_01.mkv": 12,
            "extrinsics/cam_00.mkv": 8,
            "extrinsics/cam_01.mkv": 8,
        },
        wrapper="my-recorded-set",
    )

    session = ingest(archive, "imported", sessions)

    root = sessions / "imported"
    assert (root / "intrinsic/cam_0/capture.mkv").is_file()
    assert (root / "intrinsic/cam_1/capture.mkv").is_file()
    assert (root / "extrinsic/cam_0.mkv").is_file()
    assert (root / "extrinsic/cam_1.mkv").is_file()

    # The remuxed video keeps the source geometry/content (no re-encode).
    props = video_properties(root / "intrinsic/cam_0/capture.mkv")
    assert (props.width, props.height, props.frames) == (64, 48, 10)

    # Frame-synchronized premise: one shared reference fps, identical stamps.
    stamps = read_timestamps(root / "extrinsic/cam_0.timestamps")
    assert len(stamps) == 8
    assert stamps[0] == 0.0
    assert stamps[1] == pytest.approx(1 / 30, abs=1e-4)
    assert stamps == read_timestamps(root / "extrinsic/cam_1.timestamps")

    manifest = json.loads((root / "extrinsic/manifest.json").read_text())
    entry = manifest["cameras"][0]
    assert entry["name"] == "cam_0"
    assert entry["video"] == "cam_0.mkv"
    assert entry["timestamps"] == "cam_0.timestamps"
    assert (entry["width"], entry["height"], entry["frames"]) == (64, 48, 8)

    # Session persisted last, canonical fields, reloads cleanly.
    loaded = load_session(sessions, "imported")
    assert loaded.mode.value == "load-from-files"
    assert loaded.step.value == "intrinsic_board"
    assert [c.name for c in loaded.cameras] == ["cam_0", "cam_1"]
    camera = loaded.cameras[0]
    assert (camera.width, camera.height, camera.fps) == (64, 48, 30)
    assert camera.resize_factor == 1.0
    assert camera.device_node == ""
    assert camera.device_path.startswith("import:")
    assert session.session_id == "imported"


@requires_ffmpeg
def test_ingest_accepts_tar_archive(tmp_path: Path) -> None:
    # Same contract, tar container: a .tar.gz of the session tree imports too.
    stage = _write_tree(
        tmp_path / "stage-tar", {"intrinsics/cam_0.mkv": 4, "extrinsics/cam_0.mkv": 4}
    )
    archive = tmp_path / "set.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(stage, arcname=".")
    session = ingest(archive, "from_tar", tmp_path / "sessions")
    root = tmp_path / "sessions/from_tar"
    assert (root / "intrinsic/cam_0/capture.mkv").is_file()
    assert (root / "extrinsic/cam_0.mkv").is_file()
    assert [c.name for c in session.cameras] == ["cam_0"]


@requires_ffmpeg
def test_ingest_intrinsics_only(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    archive = _make_zip(tmp_path, "intr", {"intrinsics/cam_0.mkv": 6})
    session = ingest(archive, "intr_only", sessions)
    root = sessions / "intr_only"
    assert (root / "intrinsic/cam_0/capture.mkv").is_file()
    assert (root / "extrinsic").is_dir()  # canonical shape, empty phase
    assert not (root / "extrinsic/manifest.json").exists()
    assert [c.name for c in session.cameras] == ["cam_0"]


@requires_ffmpeg
def test_ingest_sidecars_from_caliscope_csv(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    rows = "".join(f"{cam},{100.0 + i / 25:.6f}\n" for i in range(6) for cam in (0, 1))
    archive = _make_zip(
        tmp_path,
        "csv",
        {
            "intrinsics/cam_0.mkv": 6,
            "intrinsics/cam_1.mkv": 6,
            "extrinsics/cam_0.mkv": 6,
            "extrinsics/cam_1.mkv": 6,
            "extrinsics/timestamps.csv": "cam_id,frame_time\n" + rows,
        },
    )
    ingest(archive, "from_csv", sessions)
    stamps = read_timestamps(sessions / "from_csv/extrinsic/cam_0.timestamps")
    assert len(stamps) == 6
    assert stamps[0] == pytest.approx(100.0)
    assert stamps == sorted(stamps)


@requires_ffmpeg
def test_ingest_ignores_csv_rows_for_absent_cameras(tmp_path: Path) -> None:
    """A Caliscope csv may cover more cameras than the archive ships (subset export)."""
    rows = "".join(f"{cam},{50.0 + i / 30:.6f}\n" for i in range(4) for cam in (0, 1, 7))
    archive = _make_zip(
        tmp_path,
        "subset",
        {
            "intrinsics/cam_0.mkv": 4,
            "intrinsics/cam_1.mkv": 4,
            "extrinsics/cam_0.mkv": 4,
            "extrinsics/cam_1.mkv": 4,
            "extrinsics/timestamps.csv": "cam_id,frame_time\n" + rows,
        },
    )
    session = ingest(archive, "subset_csv", tmp_path / "sessions")
    assert [c.name for c in session.cameras] == ["cam_0", "cam_1"]
    sweep = tmp_path / "sessions/subset_csv/extrinsic"
    assert len(read_timestamps(sweep / "cam_0.timestamps")) == 4
    assert not (sweep / "cam_7.timestamps").exists()


@requires_ffmpeg
def test_ingest_rejects_csv_row_mismatch(tmp_path: Path) -> None:
    # 45 rows for a 6-frame video is way past the tail-drift tolerance: misaligned.
    rows = "".join(f"0,{i / 30:.6f}\n" for i in range(45))
    archive = _make_zip(
        tmp_path,
        "short",
        {
            "intrinsics/cam_0.mkv": 6,
            "extrinsics/cam_0.mkv": 6,
            "extrinsics/timestamps.csv": "cam_id,frame_time\n" + rows,
        },
    )
    with pytest.raises(ImportValidationError, match="rows for cam_0"):
        ingest(archive, "short_csv", tmp_path / "sessions")


def test_sidecar_times_tolerates_tail_drift(tmp_path: Path) -> None:
    # Real recorders disagree with their csv by a few trailing frames: one stamp
    # too many is truncated (never reference a frame past EOF); one too few keeps
    # the unstamped tail frame out of the sync groups.
    rows = "".join(f"0,{i / 30:.6f}\n" for i in range(5)) + "".join(
        f"1,{i / 30:.6f}\n" for i in range(3)
    )
    plan = _csv_plan(tmp_path, "cam_id,frame_time\n" + rows, [0, 1])
    props = {0: VideoProperties(64, 48, 30.0, 4), 1: VideoProperties(64, 48, 30.0, 4)}
    times = _sidecar_times(plan, props)
    assert len(times[0]) == 4  # 5 stamps, 4 frames -> tail stamp dropped
    assert len(times[1]) == 3  # 3 stamps, 4 frames -> kept as-is


@requires_ffmpeg
def test_ingest_rejects_resolution_mismatch(tmp_path: Path) -> None:
    stage = tmp_path / "stage-res"
    _make_video(stage / "intrinsics/cam_0.mkv", 4, size=(64, 48))
    _make_video(stage / "extrinsics/cam_0.mkv", 4, size=(96, 64))
    archive = tmp_path / "res.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                bundle.write(file, str(file.relative_to(stage)))
    with pytest.raises(ImportValidationError, match="matching resolutions"):
        ingest(archive, "res_mismatch", tmp_path / "sessions")


@requires_ffmpeg
def test_ingest_aligns_disparate_cadences_without_csv(tmp_path: Path) -> None:
    # Different fps/counts no longer reject: the caliscope-parity alignment
    # normalises every camera onto shared sync slots (ADR-0035 follow-up).
    stage = tmp_path / "stage-fps"
    _make_video(stage / "intrinsics/cam_0.mkv", 4, fps=30)
    _make_video(stage / "intrinsics/cam_1.mkv", 4, fps=15)
    _make_video(stage / "extrinsics/cam_0.mkv", 4, fps=30)
    _make_video(stage / "extrinsics/cam_1.mkv", 4, fps=15)
    archive = tmp_path / "fps.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for file in sorted(stage.rglob("*")):
            if file.is_file():
                bundle.write(file, str(file.relative_to(stage)))
    session = ingest(archive, "fps_mix", tmp_path / "sessions")
    assert [c.name for c in session.cameras] == ["cam_0", "cam_1"]
    sweep = tmp_path / "sessions/fps_mix/extrinsic"
    a = read_timestamps(sweep / "cam_0.timestamps")
    b = read_timestamps(sweep / "cam_1.timestamps")
    # Same counts here -> identity slots: shared stamps despite the fps mismatch.
    assert len(a) == len(b) == 4
    assert a == b


# --- Sidecar synthesis from a csv (pure: no ffmpeg) --------------------------------


def _csv_plan(tmp_path: Path, csv_text: str, indices: list[int]) -> ImportPlan:
    csv_path = tmp_path / "timestamps.csv"
    csv_path.write_text(csv_text)
    videos = tuple(PlannedVideo(index=i, source=tmp_path / f"cam_{i}.mkv") for i in indices)
    return ImportPlan(intrinsic=videos, extrinsic=videos, timestamps_csv=csv_path)


def test_sidecar_times_remaps_constant_offset_csv_ids(tmp_path: Path) -> None:
    # Renumbered files vs original ids (1-based Caliscope ports vs 0-based files):
    # a constant shift between the sorted sets is deterministic -> remapped.
    rows = "".join(f"{cam},{i / 30:.6f}\n" for i in range(3) for cam in (1, 2))
    plan = _csv_plan(tmp_path, "cam_id,frame_time\n" + rows, [0, 1])
    props = {0: VideoProperties(64, 48, 30.0, 3), 1: VideoProperties(64, 48, 30.0, 3)}
    times = _sidecar_times(plan, props)
    assert set(times) == {0, 1}
    assert len(times[0]) == 3 and times[0] == sorted(times[0])


def test_sidecar_times_ignores_inferred_grid_and_aligns_caliscope_style(tmp_path: Path) -> None:
    # Caliscope's inferred_timestamps.csv: one perfectly constant delta per camera
    # -> not capture times (time-based pairing on it mis-synchronizes, 10 px bug).
    # The csv is ignored and the videos are aligned the way caliscope does it.
    rows = "".join(f"0,{i * 0.033178:.9f}\n" for i in range(120)) + "".join(
        f"1,{i * 0.033490:.9f}\n" for i in range(120)
    )
    plan = _csv_plan(tmp_path, "cam_id,frame_time\n" + rows, [0, 1])
    props = {0: VideoProperties(64, 48, 30.0, 120), 1: VideoProperties(64, 48, 30.0, 120)}
    times = _sidecar_times(plan, props)
    # Equal counts -> identity slots: both sidecars share the exact same stamps.
    assert times[0] == times[1]
    assert times[0][0] == 0.0 and times[0][1] == pytest.approx(1 / 30, rel=1e-3)


def test_sidecar_times_accepts_real_jittery_timestamps(tmp_path: Path) -> None:
    # Real capture logs jitter by design; a jittered series must pass the guard.
    rng = [i * 0.0333 + (0.0004 if i % 7 == 0 else 0.0) for i in range(120)]
    rows = "".join(f"{cam},{t:.9f}\n" for t in rng for cam in (0, 1))
    plan = _csv_plan(tmp_path, "cam_id,frame_time\n" + rows, [0, 1])
    props = {0: VideoProperties(64, 48, 30.0, 120), 1: VideoProperties(64, 48, 30.0, 120)}
    times = _sidecar_times(plan, props)
    assert len(times[0]) == 120 and len(times[1]) == 120


def test_sidecar_times_rejects_non_constant_csv_ids(tmp_path: Path) -> None:
    # {0,5} vs videos {0,1} is NOT a constant shift: no guessing -> explicit error.
    rows = "".join(f"{cam},{i / 30:.6f}\n" for i in range(3) for cam in (0, 5))
    plan = _csv_plan(tmp_path, "cam_id,frame_time\n" + rows, [0, 1])
    props = {0: VideoProperties(64, 48, 30.0, 3), 1: VideoProperties(64, 48, 30.0, 3)}
    with pytest.raises(ImportValidationError, match="no rows for cam_1"):
        _sidecar_times(plan, props)


# --- Camera Setup confirmation (the import flow's "Continue", ADR-0035) -----------


def test_confirm_camera_setup_transitions(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "imported")
    session = manager.current()
    session.cameras = [
        CameraConfig(
            index=0,
            name="cam_0",
            prefix="cam",
            device_path="import:cam_0.mkv",
            device_node="",
            width=64,
            height=48,
            resize_factor=1.0,
            fps=30,
        )
    ]

    # Boards not defined yet -> refused (wizard order stays enforceable server-side).
    with pytest.raises(ValueError, match="boards"):
        manager.confirm_camera_setup()

    session.step = WizardStep.CAMERA_SETUP
    assert manager.confirm_camera_setup().step is WizardStep.INTRINSIC_CAPTURE
    # Idempotent once past: never regresses a more advanced step.
    assert manager.confirm_camera_setup().step is WizardStep.INTRINSIC_CAPTURE
    session.step = WizardStep.EXPORT
    assert manager.confirm_camera_setup().step is WizardStep.EXPORT


def test_confirm_camera_setup_requires_cameras(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, "empty")
    manager.current().step = WizardStep.CAMERA_SETUP
    with pytest.raises(ValueError, match="no cameras"):
        manager.confirm_camera_setup()


@requires_ffmpeg
def test_failed_ingest_leaves_no_partial_session(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    misaligned = "".join(f"0,{i / 30:.6f}\n" for i in range(45))  # way past tail drift
    archive = _make_zip(
        tmp_path,
        "bad",
        {
            "intrinsics/cam_0.mkv": 6,
            "extrinsics/cam_0.mkv": 6,
            "extrinsics/timestamps.csv": "cam_id,frame_time\n" + misaligned,
        },
    )
    with pytest.raises(ImportValidationError):
        ingest(archive, "broken", sessions)
    assert not (sessions / "broken").exists()
    assert list_sessions(sessions) == []
