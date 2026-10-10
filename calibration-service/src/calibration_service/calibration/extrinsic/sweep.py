"""A recorded sweep: sync window, groups, detection, motion gate (ADR-0007, ADR-0056)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
from numpy.typing import NDArray

from calibration_service.calibration.extrinsic.model import (
    CameraModel,
    GroupDetection,
    _min_corners,
)
from calibration_service.calibration.motion import (
    MemberMotion,
    group_motion_px,
    lk_speed_px_s,
    still_groups,
    track_corners_lk,
)
from calibration_service.detection import BoardDetector
from calibration_service.models.board import BoardType, CalibrationBoard
from calibration_service.recording import read_timestamps
from calibration_service.session.layout import SWEEP_MANIFEST
from calibration_service.synchronization import SyncFrame, SyncGroup
from calibration_service.synchronization.window import sync_window

logger = logging.getLogger(__name__)


def derive_sweep_window(directory: Path, names: list[str]) -> float:
    """Sync window derived from the RECORDED cadence, not the configured fps.

    The capture loop's effective write rate can be well below the camera fps
    (detection/encode contention) — observed ~18 fps for a 30 fps config. A window
    below one REAL frame interval keeps pairing unambiguous (ADR-0007 intent):
    0.95 x the slowest camera's mean recorded period, clamped to sane bounds.

    The mean period (span / intervals), not the median interval: uvcvideo's kernel
    stamps (ADR-0049) land on a coarse grid, so a 30 fps sweep records intervals of
    20, 24, 40 and 44 ms whose median is 40 ms. The median widened the window to
    38 ms, past one frame period: on a real sweep the group spread p95 went from
    21 to 48 ms. The span keeps the cadence exact whatever the per-frame jitter.
    """
    periods: list[float] = []
    for name in names:
        path = directory / f"{name}.timestamps"
        if not path.is_file():
            continue  # camera missing from the sweep: sync simply excludes it
        stamps = read_timestamps(path)
        if len(stamps) >= 2:
            periods.append((stamps[-1] - stamps[0]) / (len(stamps) - 1))
    if not periods:
        raise ValueError(f"no recorded timestamps under {directory}")
    # Same derivation rule as the live synchronizer (ADR-0037): one truth.
    return sync_window(max(periods))


class _Detected(NamedTuple):
    """The compute's detection walk: groups seen by >= 2 cameras, aligned lists."""

    groups: list[dict[str, GroupDetection]]
    motions: list[dict[str, MemberMotion]]  # per member: capture stamp + board speed
    refusals: dict[str, dict[str, int]]  # per camera, by reason (ADR-0052)
    attempts: dict[str, int]  # per camera, views the border refinement ran on


@dataclass
class _Pending:
    """A detected member waiting for the next frame to finish its board speed."""

    positions: list[int]
    gray: NDArray[np.uint8]
    corners: NDArray[np.float64]
    in_previous: NDArray[np.float64] | None
    frame: int


def _gray(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), np.uint8)


def _member_motion(
    item: _Pending, in_next: NDArray[np.float64] | None, stamps: list[float]
) -> MemberMotion:
    """Capture stamp and LK board speed of a pending member, once its next frame is read."""
    i = item.frame
    if i >= len(stamps):  # a frame past its sidecar: no stamp, no speed
        return MemberMotion(timestamp=float("nan"), speed_px_s=None)
    dt_previous = stamps[i] - stamps[i - 1] if i > 0 and item.in_previous is not None else None
    dt_next = stamps[i + 1] - stamps[i] if in_next is not None and i + 1 < len(stamps) else None
    speed = lk_speed_px_s(item.corners, item.in_previous, in_next, dt_previous, dt_next)
    return MemberMotion(timestamp=stamps[i], speed_px_s=speed)


def _detect_group_frames(
    directory: Path,
    groups_frames: list[dict[str, int]],
    models: dict[str, CameraModel],
    board: CalibrationBoard,
) -> _Detected:
    """Detect the board on each selected (camera, frame-index) and normalize corners.

    Also measures each detected member's board image speed (ADR-0056): its corners
    are tracked by Lucas-Kanade into the previous frame and the next one, which the
    sequential decode walks anyway; and counts, per camera, the single-marker views
    the border refinement dropped by reason, out of the views it ran on.

    Single-ArUco targets: the detector reports every corner under the marker id
    (e.g. [8,8,8,8]); remap to per-CORNER ids 0..3 (cv2's TL,TR,BR,BL order is
    stable across views) so cross-camera correspondence + ``board_object_points``
    indexing work like the ChArUco path.
    """
    single_marker = board.board_type is not BoardType.CHARUCO
    min_corners = _min_corners(board)
    needed: dict[str, list[tuple[int, int]]] = {}
    for position, group in enumerate(groups_frames):
        for name, frame_index in group.items():
            needed.setdefault(name, []).append((frame_index, position))

    detections: list[dict[str, GroupDetection]] = [{} for _ in groups_frames]
    motions: list[dict[str, MemberMotion]] = [{} for _ in groups_frames]
    refusals: dict[str, dict[str, int]] = {}
    attempts: dict[str, int] = {}
    for name, entries in needed.items():
        model = models[name]
        stamps = read_timestamps(directory / f"{name}.timestamps")
        # The compute's detector (unbiased single-marker corners, ADR-0052), one per
        # camera: its refusal counters are that camera's.
        detector = BoardDetector(board, border_refine=True)
        capture = cv2.VideoCapture(str(directory / f"{name}.mkv"))
        try:
            # SEQUENTIAL walk — never CAP_PROP_POS_FRAMES. Index seeking on a VFR
            # source (imported remuxes, ADR-0035) lands on the WRONG frame: OpenCV
            # maps the index to a time via the AVERAGE fps, drifting by whole
            # frames wherever the real cadence deviates (measured +1..+21 frames
            # on a real Caliscope import — the sole cause of a 10 px vs 3.7 px
            # extrinsic RMSE). Sidecar line i MUST mean decoded frame i. The walk
            # reads one frame past the last wanted one, for its forward track.
            wanted = sorted(entries)
            cursor = 0
            current = -1
            previous: NDArray[np.uint8] | None = None
            pending: _Pending | None = None
            while cursor < len(wanted) or pending is not None:
                ok, frame = capture.read()
                if not ok or frame is None:
                    break
                current += 1
                # cv2's stubs type read() loosely; decoded frames are uint8 BGR.
                image = frame.astype(np.uint8, copy=False)
                is_wanted = cursor < len(wanted) and current == wanted[cursor][0]
                gray = _gray(image) if pending is not None or is_wanted else None
                if pending is not None and gray is not None:
                    forward = track_corners_lk(pending.gray, gray, pending.corners)
                    motion = _member_motion(pending, forward, stamps)
                    for position in pending.positions:
                        motions[position][name] = motion
                    pending = None
                if is_wanted and gray is not None:
                    detection = detector.detect(gray)
                    positions: list[int] = []
                    while cursor < len(wanted) and wanted[cursor][0] == current:
                        positions.append(wanted[cursor][1])
                        cursor += 1
                    usable = (
                        detection.found
                        and detection.ids is not None
                        and detection.corners is not None
                        and detection.count >= min_corners
                    )
                    if usable:
                        assert detection.ids is not None and detection.corners is not None
                        if single_marker:
                            ids = np.arange(detection.count, dtype=np.int32)  # corner index
                            pixels = detection.corners.reshape(-1, 2).astype(np.float64)
                        else:
                            ids = detection.ids.reshape(-1).astype(np.int32)
                            order = np.argsort(ids)  # sorted: searchsorted in stereo_pairwise
                            ids = ids[order]
                            pixels = detection.corners.reshape(-1, 2).astype(np.float64)[order]
                        normalized = cv2.undistortPoints(
                            pixels.reshape(-1, 1, 2), model.matrix, model.distortions
                        ).reshape(-1, 2)
                        for position in positions:
                            detections[position][name] = GroupDetection(
                                ids=ids,
                                corners_px=pixels,
                                corners_norm=np.asarray(normalized, np.float64),
                                sharpness=detection.sharpness,
                            )
                        # LK follows the detector's own corner order (same points).
                        raw = detection.corners.reshape(-1, 2).astype(np.float64)
                        backward = (
                            track_corners_lk(gray, _gray(previous), raw)
                            if previous is not None
                            else None
                        )
                        pending = _Pending(positions, gray, raw, backward, current)
                previous = image
            if pending is not None:  # the video ended: a one-sided speed
                motion = _member_motion(pending, None, stamps)
                for position in pending.positions:
                    motions[position][name] = motion
        finally:
            capture.release()
        if detector.border_attempts:
            attempts[name] = detector.border_attempts
        if detector.border_refusals:
            refusals[name] = dict(detector.border_refusals)
    if refusals:
        logger.info(
            "border refinement dropped %d of %d views: %s",
            sum(sum(counts.values()) for counts in refusals.values()),
            sum(attempts.values()),
            ", ".join(
                f"{name} {sum(counts.values())}/{attempts.get(name, 0)} {counts}"
                for name, counts in sorted(refusals.items())
            ),
        )
    kept = [i for i, group in enumerate(detections) if len(group) >= 2]
    return _Detected(
        groups=[detections[i] for i in kept],
        motions=[{n: m for n, m in motions[i].items() if n in detections[i]} for i in kept],
        refusals=refusals,
        attempts=attempts,
    )


def _motion_gate(
    detected: _Detected, max_motion_px: float | None, max_groups: int
) -> tuple[list[dict[str, GroupDetection]], int]:
    """The detected groups the motion gate keeps (ADR-0056), and how many it dropped.

    A quarter of the group budget is guaranteed: below it, the stillest dropped groups
    top the pool up instead of starving the pairwise initialisation.
    """
    if max_motion_px is None:
        return detected.groups, 0
    motion = [group_motion_px(members) for members in detected.motions]
    keep = still_groups(motion, max_motion_px=max_motion_px, min_groups=max(1, max_groups // 4))
    pool = [group for group, kept in zip(detected.groups, keep, strict=True) if kept]
    moving = len(keep) - len(pool)
    if moving:
        logger.info(
            "motion gate: dropped %d of %d detected groups (board moved > %.2f px)",
            moving,
            len(keep),
            max_motion_px,
        )
    return pool, moving


def _select_quality_groups(
    detections: list[dict[str, GroupDetection]], cap: int
) -> list[dict[str, GroupDetection]]:
    """Keep the ~``cap`` sharpest groups, spread over time (ADR-0033).

    A group is only as good as its blurriest member (min sharpness across
    cameras): motion blur puts corners off by pixels, and one bad view poisons
    the pair. Temporal bins (like ``_diverse_group_indices``) keep the sweep's
    spatial diversity; within each bin the sharpest group wins.
    """
    if len(detections) <= cap:
        return detections
    bins: dict[int, tuple[int, float]] = {}
    span = len(detections)
    for position, group in enumerate(detections):
        score = min(member.sharpness for member in group.values())
        bin_id = min(cap - 1, position * cap // span)
        best = bins.get(bin_id)
        if best is None or score > best[1]:
            bins[bin_id] = (position, score)
    keep = sorted(position for position, _ in bins.values())
    return [detections[position] for position in keep]


def sweep_groups(directory: Path, names: list[str], window_s: float) -> list[SyncGroup[int]]:
    """Synchronize a recorded sweep on its timestamp sidecars alone (no decoding).

    Offline grouping is **nearest-neighbour matching onto a reference timeline**
    (the camera with the most frames), NOT the live head-greedy pairing: real rigs
    record at per-camera EFFECTIVE rates that differ by 20%+ (frames skipped under
    load), and greedy head-windowing then fragments one physical instant into
    arbitrary small pairs — grouping a camera that sees the board with one that
    doesn't (real-rig bug). Here every reference frame becomes a candidate instant
    and each other camera contributes its closest unused frame within the window,
    so instants stay COMPLETE; a camera frame is consumed at most once.

    Payloads are per-camera frame indices; the same groups drive the Prepare
    scrubber (``GET /extrinsic/groups``) and the compute selection, so what the
    operator scrubs is exactly what the solver consumes.
    """
    stamps = {
        name: read_timestamps(directory / f"{name}.timestamps")
        for name in names
        if (directory / f"{name}.timestamps").is_file()
    }
    stamps = {name: series for name, series in stamps.items() if series}
    if not stamps:
        raise ValueError(f"no recorded timestamps under {directory}")

    reference = max(stamps, key=lambda name: len(stamps[name]))
    pointers = dict.fromkeys((n for n in stamps if n != reference), 0)

    groups: list[SyncGroup[int]] = []
    for ref_index, ref_time in enumerate(stamps[reference]):
        members = {reference: SyncFrame(reference, ref_time, ref_index)}
        for name, cursor in pointers.items():
            series = stamps[name]
            # Advance to this camera's frame closest to the reference instant.
            while cursor + 1 < len(series) and abs(series[cursor + 1] - ref_time) <= abs(
                series[cursor] - ref_time
            ):
                cursor += 1
            pointers[name] = cursor
            if cursor < len(series) and abs(series[cursor] - ref_time) <= window_s:
                members[name] = SyncFrame(name, series[cursor], cursor)
                pointers[name] = cursor + 1  # consumed: one group per frame
        if len(members) >= 2:
            timestamps = [frame.timestamp for frame in members.values()]
            groups.append(
                SyncGroup(
                    frames=members,
                    timestamp=sum(timestamps) / len(timestamps),
                    spread=max(timestamps) - min(timestamps),
                )
            )
    return groups


def _warn_on_mixed_clocks(directory: Path) -> None:
    """Warn when the sweep's cameras were not all stamped on the same base (ADR-0049).

    A camera on host timestamps against others on kernel ones carries a 20 to
    44 ms bias in every group; a "mixed" camera was reopened mid-sweep on another
    base. The solve still runs: the manifest says why it may be poorer.
    """
    try:
        manifest = json.loads((directory / SWEEP_MANIFEST).read_text())
        clocks = {str(c["name"]): str(c.get("clock", "unknown")) for c in manifest["cameras"]}
    except (OSError, ValueError, KeyError, TypeError):
        return
    if "mixed" in clocks.values() or len(set(clocks.values()) - {"unknown"}) > 1:
        logger.warning("sweep cameras were stamped on different time bases: %s", clocks)
