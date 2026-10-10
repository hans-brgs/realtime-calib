"""Ingest a pre-recorded session archive into the canonical session layout (ADR-0035).

"Load from files": the operator uploads a ZIP or tar(.gz/.bz2/.xz) of
already-captured videos (``intrinsics/cam_<n>.<ext>``, ``extrinsics/cam_<n>.<ext>``,
optional Caliscope ``timestamps.csv``). Ingest extracts it safely (zip-slip guard /
tar ``data`` filter, PEP 706), validates the
naming/format contract, normalises every video into the canonical layout — a
container remux by default, **no re-encode** (frames preserved bit-for-bit) —
synthesises or imports the extrinsic timestamp sidecars (seconds, ADR-0007), and
materialises a ``load-from-files`` session. Downstream (offline compute, preview
transcode, wizard) then works unchanged; capture stays neutralised.

Everything here is synchronous by design: the API runs ``ingest`` in an executor,
off the event loop, exactly like the intrinsic/extrinsic compute paths.
"""

from __future__ import annotations

import logging
import math
import shutil
import tempfile
from json import dumps
from pathlib import Path

from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
    SessionMode,
    WizardStep,
)
from calibration_service.recording.extrinsic_recorder import (
    extrinsic_dir,
)
from calibration_service.recording.ffmpeg import (
    reencode_args,
    remux_copy_args,
    run_ffmpeg,
    transcode_timeout,
)
from calibration_service.recording.replay import (
    VideoProperties,
    declared_fps,
    video_properties,
)
from calibration_service.recording.video_writer import intrinsic_capture_path
from calibration_service.session.import_contract import (
    CAMERA_PREFIX,
    ImportPlan,
    ImportValidationError,
    PlannedVideo,
    UnreadableArchiveError,
    _extract_archive,
    parse_camera_index,
    plan_import,
)
from calibration_service.session.import_sidecars import _sidecar_times
from calibration_service.session.layout import SWEEP_MANIFEST
from calibration_service.session.manager import validate_session_id
from calibration_service.session.store import SESSION_FILE, save_session, session_dir
from calibration_service.tuning import TUNING

logger = logging.getLogger(__name__)

# The archive contract and the sidecars live in their own modules (EXP-19); their
# names stay importable from here.
__all__ = [
    "ImportPlan",
    "ImportValidationError",
    "PlannedVideo",
    "UnreadableArchiveError",
    "ingest",
    "parse_camera_index",
    "plan_import",
]


def _probe_or_none(path: Path) -> VideoProperties | None:
    """Probe a normalised video; ``None`` when unreadable or missing size/fps/frames."""
    try:
        props = video_properties(path)
    except ValueError:
        return None
    usable = props.width > 0 and props.height > 0 and props.fps > 0 and props.frames > 0
    return props if usable else None


def _duration_s(source: Path) -> float:
    """The media duration, for the transcode timeout; 0 when it cannot be read."""
    try:
        props = video_properties(source)
    except ValueError:
        return 0.0
    return props.frames / props.fps if props.fps > 0 and props.frames > 0 else 0.0


def _normalise_video(source: Path, destination: Path) -> VideoProperties:
    """Bring one uploaded video into the canonical layout (ADR-0035).

    Remux ``-c copy`` (container only, frames untouched, a variable frame rate kept:
    every compute decodes sequentially, never by time); re-encode to MJPG, frame for
    frame, only when the remuxed file does not probe usable (ADR-0054).
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    timeout_s = transcode_timeout(_duration_s(source))
    run_ffmpeg(remux_copy_args(source, destination), timeout_s=timeout_s)
    props = _probe_or_none(destination)
    if props is not None:
        return props
    logger.warning("remuxed %s is unreadable; falling back to a re-encode", source.name)
    destination.unlink(missing_ok=True)
    # Any positive rate re-times by index; the source's own one keeps durations sane.
    fps = declared_fps(source)
    if not (math.isfinite(fps) and fps > 0.0):
        fps = float(TUNING.default_fps)
    run_ffmpeg(reencode_args(source, destination, fps), timeout_s=timeout_s)
    props = _probe_or_none(destination)
    if props is None:
        raise ImportValidationError(f"cannot read video {source.name!r} after normalisation")
    return props


def _write_manifest(directory: Path, plan: ImportPlan, props: dict[int, VideoProperties]) -> None:
    """Same manifest schema as a live sweep (ExtrinsicRecorder.close)."""
    manifest = {
        "cameras": [
            {
                "name": f"{CAMERA_PREFIX}_{video.index}",
                "video": f"{CAMERA_PREFIX}_{video.index}.mkv",
                "timestamps": f"{CAMERA_PREFIX}_{video.index}.timestamps",
                "width": props[video.index].width,
                "height": props[video.index].height,
                "fps": max(1, round(props[video.index].fps)),
                "frames": props[video.index].frames,
                # Imported timestamps: their base is not known (ADR-0049).
                "clock": "unknown",
            }
            for video in plan.extrinsic
        ]
    }
    (directory / SWEEP_MANIFEST).write_text(dumps(manifest, indent=2))


def _camera_configs(
    plan: ImportPlan, intrinsic_props: dict[int, VideoProperties]
) -> list[CameraConfig]:
    """Derive the session's cameras from the intrinsic videos (the calibration
    resolution, ADR-0015). No live device: node empty, source name kept as the path."""
    return [
        CameraConfig(
            index=video.index,
            name=f"{CAMERA_PREFIX}_{video.index}",
            prefix=CAMERA_PREFIX,
            device_path=f"import:{video.source.name}",
            device_node="",
            width=intrinsic_props[video.index].width,
            height=intrinsic_props[video.index].height,
            resize_factor=1.0,
            fps=max(1, round(intrinsic_props[video.index].fps)),
            status=CameraStatus.CONFIGURED,
        )
        for video in plan.intrinsic
    ]


def _materialize(plan: ImportPlan, sessions_dir: Path, session_id: str) -> list[CameraConfig]:
    """Write videos/sidecars/manifest into the canonical session folder."""
    target = session_dir(sessions_dir, session_id)
    (target / "intrinsic").mkdir(parents=True, exist_ok=True)
    (target / "extrinsic").mkdir(parents=True, exist_ok=True)

    intrinsic_props: dict[int, VideoProperties] = {}
    for video in plan.intrinsic:
        name = f"{CAMERA_PREFIX}_{video.index}"
        destination = intrinsic_capture_path(sessions_dir, session_id, name)
        intrinsic_props[video.index] = _normalise_video(video.source, destination)

    sweep_dir = extrinsic_dir(sessions_dir, session_id)
    extrinsic_props: dict[int, VideoProperties] = {}
    for video in plan.extrinsic:
        destination = sweep_dir / f"{CAMERA_PREFIX}_{video.index}.mkv"
        props = _normalise_video(video.source, destination)
        expected = intrinsic_props[video.index]
        if (props.width, props.height) != (expected.width, expected.height):
            raise ImportValidationError(
                f"cam_{video.index}: intrinsic video is {expected.width}x{expected.height} "
                f"but the extrinsic video is {props.width}x{props.height}; the solver needs "
                "matching resolutions"
            )
        extrinsic_props[video.index] = props

    if plan.extrinsic:
        times = _sidecar_times(plan, extrinsic_props, sweep_dir)
        for video in plan.extrinsic:
            sidecar = sweep_dir / f"{CAMERA_PREFIX}_{video.index}.timestamps"
            sidecar.write_text(
                "".join(f"{t:.6f}\n" for t in times[video.index]), encoding="ascii"
            )
        _write_manifest(sweep_dir, plan, extrinsic_props)

    return _camera_configs(plan, intrinsic_props)


def ingest(archive: Path, session_id: str, sessions_dir: Path) -> CalibrationSession:
    """Import a pre-recorded session archive into a fresh session folder (ADR-0035).

    Raises ``ValueError``/``ImportValidationError`` (bad name or contract, HTTP 422),
    ``FileExistsError`` (session exists, 409), ``UnreadableArchiveError`` (not a
    zip/tar, 400) or ``FfmpegError``. ``session.toml`` is written LAST, so a crashed
    import never surfaces as a session; on failure the partial folder is removed.
    """
    sid = validate_session_id(session_id)
    target = session_dir(sessions_dir, sid)
    if (target / SESSION_FILE).is_file():
        raise FileExistsError(f"session {sid!r} already exists")
    created = not target.exists()

    sessions_dir.mkdir(parents=True, exist_ok=True)
    # Extract next to the sessions (same volume as the destination, dot-prefixed so
    # the folder can never collide with a session id nor show up in listings).
    with tempfile.TemporaryDirectory(dir=sessions_dir, prefix=".import-") as scratch:
        extracted = Path(scratch)
        _extract_archive(archive, extracted)
        plan = plan_import(extracted)
        try:
            cameras = _materialize(plan, sessions_dir, sid)
            session = CalibrationSession(
                session_id=sid,
                step=WizardStep.INTRINSIC_BOARD,
                mode=SessionMode.LOAD_FROM_FILES,
                cameras=cameras,
            )
            save_session(sessions_dir, session)
        except Exception:
            if created:
                shutil.rmtree(target, ignore_errors=True)
            raise

    logger.info(
        "imported session %s: %d camera(s), %d intrinsic + %d extrinsic video(s)",
        sid,
        len(session.cameras),
        len(plan.intrinsic),
        len(plan.extrinsic),
    )
    return session
