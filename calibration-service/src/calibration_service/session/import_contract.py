"""The import archive's contract (ADR-0035): safe extraction and the validated plan.

What the upload must look like (``intrinsics/cam_<n>.<ext>``, ``extrinsics/cam_<n>.<ext>``,
an optional Caliscope ``timestamps.csv``), how it is extracted without escaping its
folder (zip-slip guard, tar ``data`` filter, PEP 706), and the inventory ``ingest``
materialises. Split from ``import_session`` (EXP-19).
"""

from __future__ import annotations

import re
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Upload contract (ADR-0035). The ZIP uses the plural spelling; the singular is
# tolerated so a Caliscope-style folder (calibration/extrinsic) drops in as-is.
_INTRINSIC_DIRS = ("intrinsics", "intrinsic")
_EXTRINSIC_DIRS = ("extrinsics", "extrinsic")
_TIMESTAMPS_FILE = "timestamps.csv"
# Container whitelist; the codec is validated implicitly (the normalised file must
# probe readable — size/fps/frames — else the import is rejected with the file named).
_VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi"})
CAMERA_RE = re.compile(r"^cam_(\d+)$")
CAMERA_PREFIX = "cam"
# Archive junk silently skipped at extraction (macOS/Windows artifacts).
_JUNK_BASENAMES = frozenset({".DS_Store", "Thumbs.db"})


class ImportValidationError(ValueError):
    """The archive violates the import contract (naming/format/sync) — HTTP 422."""


class UnreadableArchiveError(RuntimeError):
    """The upload is not a readable ZIP or tar archive — HTTP 400."""


def parse_camera_index(stem: str) -> int:
    """Camera number from a ``cam_<n>`` file stem (zero-padding tolerated)."""
    match = CAMERA_RE.fullmatch(stem)
    if match is None:
        raise ImportValidationError(f"{stem!r} does not follow the cam_<number> naming contract")
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
            raise ImportValidationError(f"unexpected folder {entry.name!r} in {directory.name}/")
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
