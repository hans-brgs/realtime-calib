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
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from json import dumps
from pathlib import Path

import numpy as np

from calibration_service.models.session import (
    CalibrationSession,
    CameraConfig,
    CameraStatus,
    SessionMode,
    WizardStep,
)
from calibration_service.recording.extrinsic_recorder import (
    extrinsic_dir,
    parse_caliscope_timestamps,
)
from calibration_service.recording.ffmpeg import (
    is_vfr,
    reencode_cfr_args,
    remux_copy_args,
    run_ffmpeg,
)
from calibration_service.recording.replay import (
    VideoProperties,
    decoded_frame_count,
    video_properties,
)
from calibration_service.recording.video_writer import intrinsic_capture_path
from calibration_service.session.manager import validate_session_id
from calibration_service.session.store import SESSION_FILE, save_session, session_dir
from calibration_service.synchronization.caliscope_alignment import (
    greedy_sync_mapping,
    inferred_grids,
)

logger = logging.getLogger(__name__)

# Upload contract (ADR-0035). The ZIP uses the plural spelling; the singular is
# tolerated so a Caliscope-style folder (calibration/extrinsic) drops in as-is.
_INTRINSIC_DIRS = ("intrinsics", "intrinsic")
_EXTRINSIC_DIRS = ("extrinsics", "extrinsic")
_TIMESTAMPS_FILE = "timestamps.csv"
# Container whitelist; the codec is validated implicitly (the normalised file must
# probe readable — size/fps/frames — else the import is rejected with the file named).
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi"})
_CAMERA_RE = re.compile(r"^cam_(\d+)$")
_CAMERA_PREFIX = "cam"
# Archive junk silently skipped at extraction (macOS/Windows artifacts).
_JUNK_BASENAMES = frozenset({".DS_Store", "Thumbs.db"})
# Real recorders and their timestamp logs disagree by a few TRAILING frames (stop
# flush; CAP_PROP_FRAME_COUNT is itself an estimate for some codecs). Tolerate up
# to ~1 s of tail drift; beyond that the csv is considered misaligned (frame i of
# the video would no longer be row i of the csv).
_ROWS_FRAMES_TOLERANCE = 30
# Last-resort sidecar cadence when a degenerate alignment has NOTHING to
# synchronize AND the videos declare no usable rate: purely arbitrary (any
# positive period does), deliberately NOT tied to the camera default_fps — this
# is not a capture cadence, just a non-zero slot spacing.
_FALLBACK_FPS = 30.0


class ImportValidationError(ValueError):
    """The archive violates the import contract (naming/format/sync) — HTTP 422."""


class UnreadableArchiveError(RuntimeError):
    """The upload is not a readable ZIP or tar archive — HTTP 400."""


def parse_camera_index(stem: str) -> int:
    """Camera number from a ``cam_<n>`` file stem (zero-padding tolerated)."""
    match = _CAMERA_RE.fullmatch(stem)
    if match is None:
        raise ImportValidationError(
            f"{stem!r} does not follow the cam_<number> naming contract"
        )
    return int(match.group(1))


@dataclass(frozen=True)
class PlannedVideo:
    """One camera video found in the archive, keyed by its parsed number."""

    index: int
    source: Path


@dataclass(frozen=True)
class ImportPlan:
    """Validated inventory of the extracted archive (what ingest will materialise)."""

    intrinsic: tuple[PlannedVideo, ...]
    extrinsic: tuple[PlannedVideo, ...]
    timestamps_csv: Path | None


def _is_junk(parts: tuple[str, ...]) -> bool:
    """macOS/Windows archive artifacts, silently skipped at extraction."""
    basename = parts[-1] if parts else ""
    return "__MACOSX" in parts or basename in _JUNK_BASENAMES or basename.startswith("._")


def _extract_archive(archive: Path, target: Path) -> None:
    """Extract a ZIP or tar(.gz/.bz2/.xz) upload — detected by content, not name."""
    if zipfile.is_zipfile(archive):
        _extract_zip(archive, target)
    elif tarfile.is_tarfile(archive):
        _extract_tar(archive, target)
    else:
        raise UnreadableArchiveError("not a readable zip or tar archive")


def _extract_zip(archive: Path, target: Path) -> None:
    """Extract a ZIP with a zip-slip guard; junk entries are skipped."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.is_dir() or _is_junk(Path(info.filename).parts):
                    continue
                # Zip-slip guard: no entry may resolve outside the extraction folder
                # (absolute paths and ".." segments both fail this check).
                destination = (target / info.filename).resolve()
                if not destination.is_relative_to(target.resolve()):
                    raise ImportValidationError(f"unsafe path in archive: {info.filename!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, destination.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
    except zipfile.BadZipFile as exc:  # headered but corrupt/truncated
        raise UnreadableArchiveError("corrupt zip archive") from exc


def _extract_tar(archive: Path, target: Path) -> None:
    """Extract a tar(.gz/.bz2/.xz) safely; junk and non-file members are skipped.

    The stdlib ``data`` extraction filter (PEP 706) is the tar counterpart of the
    zip-slip guard: it rejects absolute paths, parent escapes and unsafe links.
    """
    try:
        with tarfile.open(archive) as bundle:
            members = [
                member
                for member in bundle.getmembers()
                if member.isfile() and not _is_junk(Path(member.name).parts)
            ]
            bundle.extractall(target, members=members, filter="data")
    except tarfile.FilterError as exc:
        raise ImportValidationError(f"unsafe path in archive: {exc}") from exc
    except tarfile.TarError as exc:  # headered but corrupt/truncated
        raise UnreadableArchiveError("corrupt tar archive") from exc


def _effective_root(root: Path) -> Path:
    """Descend through single-folder wrappers (the user zipped a folder, not its content)."""
    current = root
    while True:
        entries = list(current.iterdir())
        directories = [e for e in entries if e.is_dir()]
        names = _INTRINSIC_DIRS + _EXTRINSIC_DIRS
        if any(d.name.lower() in names for d in directories):
            return current
        if len(directories) != 1 or any(e.is_file() for e in entries):
            return current
        current = directories[0]


def _find_dir(root: Path, names: tuple[str, ...]) -> Path | None:
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name.lower() in names:
            return entry
    return None


def _scan_phase(
    directory: Path, *, allow_timestamps: bool
) -> tuple[list[PlannedVideo], Path | None]:
    """Inventory one phase folder: strictly ``cam_<n>`` videos (+ optional csv).

    Strict on purpose: silently ignoring a mistyped file would surface later as a
    mysteriously missing camera — reject early with the file named instead.
    """
    videos: dict[int, PlannedVideo] = {}
    csv_path: Path | None = None
    for entry in sorted(directory.iterdir()):
        if entry.is_dir():
            raise ImportValidationError(
                f"unexpected folder {entry.name!r} in {directory.name}/"
            )
        if allow_timestamps and entry.name.lower() == _TIMESTAMPS_FILE:
            csv_path = entry
            continue
        if entry.suffix.lower() not in _VIDEO_EXTENSIONS:
            raise ImportValidationError(
                f"unsupported file {entry.name!r} in {directory.name}/ "
                f"(accepted: cam_<number> videos {', '.join(sorted(_VIDEO_EXTENSIONS))})"
            )
        index = parse_camera_index(entry.stem)
        if index in videos:
            raise ImportValidationError(
                f"duplicate camera cam_{index} in {directory.name}/ "
                f"({videos[index].source.name} and {entry.name})"
            )
        videos[index] = PlannedVideo(index=index, source=entry)
    return [videos[i] for i in sorted(videos)], csv_path


def plan_import(root: Path) -> ImportPlan:
    """Validate the extracted archive against the import contract (ADR-0035)."""
    root = _effective_root(root)
    intrinsic_folder = _find_dir(root, _INTRINSIC_DIRS)
    extrinsic_folder = _find_dir(root, _EXTRINSIC_DIRS)
    intrinsic: list[PlannedVideo] = []
    extrinsic: list[PlannedVideo] = []
    csv_path: Path | None = None
    if intrinsic_folder is not None:
        intrinsic, _ = _scan_phase(intrinsic_folder, allow_timestamps=False)
    if extrinsic_folder is not None:
        extrinsic, csv_path = _scan_phase(extrinsic_folder, allow_timestamps=True)
    if csv_path is None:  # also accepted at the archive root
        root_csv = next(
            (e for e in root.iterdir() if e.is_file() and e.name.lower() == _TIMESTAMPS_FILE),
            None,
        )
        csv_path = root_csv

    if not intrinsic and not extrinsic:
        raise ImportValidationError(
            "no intrinsics/ or extrinsics/ folder with cam_<number> videos in the archive"
        )
    if extrinsic and not intrinsic:
        # Capture is neutralised in load-from-files mode, so intrinsics could never
        # be produced later — an extrinsics-only import would be a dead end.
        raise ImportValidationError(
            "the archive has extrinsics/ videos but no intrinsics/: intrinsic "
            "calibration cannot be recorded in load-from-files mode"
        )
    if intrinsic and extrinsic:
        got_i = {v.index for v in intrinsic}
        got_e = {v.index for v in extrinsic}
        if got_i != got_e:
            missing = sorted(got_i ^ got_e)
            raise ImportValidationError(
                "intrinsics/ and extrinsics/ must cover the same cameras; mismatched: "
                + ", ".join(f"cam_{i}" for i in missing)
            )
    return ImportPlan(
        intrinsic=tuple(intrinsic), extrinsic=tuple(extrinsic), timestamps_csv=csv_path
    )


def _probe_or_none(path: Path) -> VideoProperties | None:
    """Probe a normalised video; ``None`` when unreadable or missing size/fps/frames."""
    try:
        props = video_properties(path)
    except ValueError:
        return None
    usable = props.width > 0 and props.height > 0 and props.fps > 0 and props.frames > 0
    return props if usable else None


def _source_fps(source: Path) -> float:
    try:
        fps = video_properties(source).fps
    except ValueError as exc:
        raise ImportValidationError(f"cannot read video {source.name!r}") from exc
    if fps <= 0:
        raise ImportValidationError(
            f"cannot determine the frame rate of {source.name!r}"
        )
    return fps


def _normalise_video(source: Path, destination: Path) -> VideoProperties:
    """Bring one uploaded video into the canonical layout (ADR-0035).

    Remux ``-c copy`` by default (container only, frames untouched); re-encode to
    CFR MJPG only when the source is VFR or the remuxed file does not probe usable.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not is_vfr(source):
        run_ffmpeg(remux_copy_args(source, destination))
        props = _probe_or_none(destination)
        if props is not None:
            return props
        logger.warning("remuxed %s is unreadable; falling back to CFR re-encode", source.name)
        destination.unlink(missing_ok=True)
    run_ffmpeg(reencode_cfr_args(source, destination, _source_fps(source)))
    props = _probe_or_none(destination)
    if props is None:
        raise ImportValidationError(f"cannot read video {source.name!r} after normalisation")
    return props


def _is_inferred_grid(parsed: dict[str, list[float]]) -> bool:
    """True when a timestamps.csv carries an INFERRED uniform grid, not capture times.

    Caliscope's ``inferred_timestamps.csv`` spreads each camera's frames evenly
    over the recording (one constant delta per camera, no gaps, start at 0).
    Real capture logs always jitter; a perfectly constant grid on 100+ frames is
    synthetic. Nearest-time pairing on such grids drifts by whole frames between
    cameras (measured 10.2 px vs 3.7 px extrinsic RMSE) — the import falls back
    to the Caliscope-parity alignment instead (caliscope never reads this file
    back either).
    """
    suspicious = 0
    for times in parsed.values():
        if len(times) < 100:
            continue  # too short to judge
        deltas = np.diff(np.asarray(times, np.float64))
        median = float(np.median(deltas))
        if median <= 0:
            continue
        spread = float(np.max(deltas) - np.min(deltas))
        if spread < 1e-4 * median:  # one constant delta across the whole video
            suspicious += 1
    return 0 < suspicious == len(parsed)


def _csv_camera_index(key: str) -> int:
    """Map a timestamps.csv ``cam_id`` ("0", tolerant of "cam_0") to a camera number."""
    match = _CAMERA_RE.fullmatch(key)
    if match is not None:
        return int(match.group(1))
    if key.isdigit():
        return int(key)
    raise ImportValidationError(f"timestamps.csv has an invalid cam_id {key!r}")


def _caliscope_aligned_times(
    plan: ImportPlan,
    props: dict[int, VideoProperties],
    sweep_dir: Path | None,
) -> dict[int, list[float]]:
    """Sidecar times from the Caliscope-parity videos-only alignment.

    Frames sharing a sync slot get the SAME stamp (slot * period), so the
    downstream sidecar grouping reproduces the slots exactly (spread 0). Frames
    a slot skipped (stall-breaker, rare) get a half-period offset: never an
    exact match, so they only ever fill a blank as a nearest fallback.

    Frame counts come from a full decode when the videos are available
    (``decoded_frame_count``): metadata estimates run a few frames long on
    remuxes, which shifts the grids and desynchronizes the blank positions
    (measured: pairing agreement 80% -> the estimate was the sole cause).
    """
    if sweep_dir is not None:
        counts = {
            str(video.index): decoded_frame_count(
                sweep_dir / f"{_CAMERA_PREFIX}_{video.index}.mkv"
            )
            for video in plan.extrinsic
        }
    else:  # unit tests without files on disk
        counts = {str(video.index): props[video.index].frames for video in plan.extrinsic}
    fps = {str(video.index): max(props[video.index].fps, 1e-6) for video in plan.extrinsic}
    grids = inferred_grids(counts, fps)
    slots = greedy_sync_mapping(grids)
    if not slots:
        raise ImportValidationError("could not align the extrinsic videos (no frames)")
    duration = max(series[-1] for series in grids.values() if series)
    # Slot spacing spans the recording: N slots cover [0, duration] in N-1 steps.
    # Degenerate fallback (<= 1 slot or zero duration = nothing to synchronize):
    # any positive period works, so derive it from the videos' own probed rate
    # rather than assuming 30 fps — a hand-inspected sidecar then reads true.
    # `fps` floors unusable probes at 1e-6 (a 0-fps container), which would blow
    # the period up to ~1e6 s: only trust plausible rates, else _FALLBACK_FPS.
    plausible = [rate for rate in fps.values() if rate > 1.0]
    fallback_period = 1.0 / max(plausible, default=_FALLBACK_FPS)
    period = duration / (len(slots) - 1) if len(slots) > 1 and duration > 0 else fallback_period
    times = {
        video.index: [(-1.0) for _ in range(counts[str(video.index)])]
        for video in plan.extrinsic
    }
    for slot, members in enumerate(slots):
        for key, frame in members.items():
            index = int(key)
            if frame < len(times[index]):
                times[index][frame] = slot * period
    for series in times.values():  # stall-dropped / tail frames: keep monotone
        previous = -period
        for i, value in enumerate(series):
            if value < 0:
                series[i] = previous + period / 2.0
            previous = series[i]
    logger.info(
        "caliscope-parity alignment: %d slots for %s frames",
        len(slots),
        {f"cam_{k}": v for k, v in sorted(counts.items())},
    )
    return times


def _sidecar_times(
    plan: ImportPlan,
    props: dict[int, VideoProperties],
    sweep_dir: Path | None = None,
) -> dict[int, list[float]]:
    """Per-camera frame times (seconds) for the extrinsic sidecars (ADR-0007).

    With a REAL Caliscope ``timestamps.csv`` (jittery capture times): its rows
    drive the sync, validated against each video's frame count. Without one —
    or when the csv is an inferred uniform grid (synthetic, mis-pairs cameras) —
    the videos are aligned the way Caliscope itself does it from videos alone
    (``caliscope_alignment``): consecutive consumption with blank slots.
    """
    if plan.timestamps_csv is not None:
        parsed = parse_caliscope_timestamps(plan.timestamps_csv)
        if _is_inferred_grid(parsed):
            logger.warning(
                "timestamps.csv is an inferred uniform grid (not capture times); "
                "ignoring it and aligning the videos caliscope-style instead"
            )
            return _caliscope_aligned_times(plan, props, sweep_dir)
        by_index: dict[int, list[float]] = {}
        for key, times in parsed.items():
            index = _csv_camera_index(key)
            if index in by_index:
                raise ImportValidationError(f"timestamps.csv lists cam_{index} twice")
            by_index[index] = times
        expected = {video.index for video in plan.extrinsic}

        if set(by_index) != expected and len(by_index) == len(expected):
            # Renumbered files vs original csv ids (typically 1-based Caliscope
            # ports vs our 0-based contract): remap ONLY when the two sorted sets
            # differ by one constant offset — deterministic, no guessing. The
            # per-camera rows==frames check below still guards a wrong pairing.
            pairs = list(zip(sorted(by_index), sorted(expected), strict=True))
            offsets = {csv_id - video_id for csv_id, video_id in pairs}
            if len(offsets) == 1 and offsets != {0}:
                offset = offsets.pop()
                logger.info(
                    "timestamps.csv ids look %+d-shifted; mapping cam_id N -> cam_(N%+d)",
                    offset,
                    -offset,
                )
                by_index = {csv_id - offset: times for csv_id, times in by_index.items()}

        extra = sorted(set(by_index) - expected)
        if extra:
            # A Caliscope timestamps.csv may cover more cameras than the archive
            # ships videos for (subset export): rows without a video are irrelevant
            # to the sync — drop them rather than reject the import.
            logger.info(
                "timestamps.csv covers cameras with no extrinsic video (ignored): %s",
                ", ".join(f"cam_{i}" for i in extra),
            )
            for index in extra:
                del by_index[index]
        for video in plan.extrinsic:
            rows = by_index.get(video.index)
            if rows is None:
                raise ImportValidationError(
                    f"timestamps.csv has no rows for cam_{video.index} "
                    f"(csv ids: {sorted(set(by_index))}, videos: {sorted(expected)})"
                )
            frames = props[video.index].frames
            drift = len(rows) - frames
            if abs(drift) > _ROWS_FRAMES_TOLERANCE:
                raise ImportValidationError(
                    f"timestamps.csv has {len(rows)} rows for cam_{video.index} "
                    f"but its video has {frames} frames"
                )
            if drift > 0:
                # More stamps than frames: drop the tail stamps — a sidecar line
                # must never reference a frame past the end of the video.
                by_index[video.index] = rows[:frames]
            if drift != 0:
                # Fewer stamps than frames is harmless as-is: the unstamped tail
                # frames simply never join a synchronized group.
                logger.info(
                    "cam_%d: timestamps.csv rows (%d) vs frames (%d) — tail drift tolerated",
                    video.index,
                    len(rows),
                    frames,
                )
        return by_index

    return _caliscope_aligned_times(plan, props, sweep_dir)


def _write_manifest(directory: Path, plan: ImportPlan, props: dict[int, VideoProperties]) -> None:
    """Same manifest schema as a live sweep (ExtrinsicRecorder.close)."""
    manifest = {
        "cameras": [
            {
                "name": f"{_CAMERA_PREFIX}_{video.index}",
                "video": f"{_CAMERA_PREFIX}_{video.index}.mkv",
                "timestamps": f"{_CAMERA_PREFIX}_{video.index}.timestamps",
                "width": props[video.index].width,
                "height": props[video.index].height,
                "fps": max(1, round(props[video.index].fps)),
                "frames": props[video.index].frames,
            }
            for video in plan.extrinsic
        ]
    }
    (directory / "manifest.json").write_text(dumps(manifest, indent=2))


def _camera_configs(
    plan: ImportPlan, intrinsic_props: dict[int, VideoProperties]
) -> list[CameraConfig]:
    """Derive the session's cameras from the intrinsic videos (the calibration
    resolution, ADR-0015). No live device: node empty, source name kept as the path."""
    return [
        CameraConfig(
            index=video.index,
            name=f"{_CAMERA_PREFIX}_{video.index}",
            prefix=_CAMERA_PREFIX,
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
        name = f"{_CAMERA_PREFIX}_{video.index}"
        destination = intrinsic_capture_path(sessions_dir, session_id, name)
        intrinsic_props[video.index] = _normalise_video(video.source, destination)

    sweep_dir = extrinsic_dir(sessions_dir, session_id)
    extrinsic_props: dict[int, VideoProperties] = {}
    for video in plan.extrinsic:
        destination = sweep_dir / f"{_CAMERA_PREFIX}_{video.index}.mkv"
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
            sidecar = sweep_dir / f"{_CAMERA_PREFIX}_{video.index}.timestamps"
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
