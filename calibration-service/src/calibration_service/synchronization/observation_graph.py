"""Co-visibility bookkeeping over synchronized groups ([[extrinsic-calibration-flow]]).

Counts, per camera pair, how many synchronized groups saw the board in BOTH
cameras — the signal that a pair can be stereo-initialised (ADR-0023) and the
live gauge telling the operator which pairs still need joint views. Also feeds
the anchor guard-rail (ADR-0012): a weakly-connected anchor is detectable from
its pair counts before the solve.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def pair_key(a: str, b: str) -> tuple[str, str]:
    """Canonical (sorted) key for an unordered camera pair."""
    return (a, b) if a <= b else (b, a)


@dataclass
class CovisibilityGraph:
    """Pairwise co-visibility counts accumulated over synchronized groups."""

    cameras: list[str]
    pair_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    synced_groups: int = 0  # groups meeting quorum, regardless of detections
    board_frames: dict[str, int] = field(default_factory=dict)  # found=True per camera

    def record(self, found_by_camera: dict[str, bool]) -> None:
        """Account one synchronized group (camera -> board found in that frame)."""
        self.synced_groups += 1
        seeing = sorted(name for name, found in found_by_camera.items() if found)
        for name in seeing:
            self.board_frames[name] = self.board_frames.get(name, 0) + 1
        for i, a in enumerate(seeing):
            for b in seeing[i + 1 :]:
                key = pair_key(a, b)
                self.pair_counts[key] = self.pair_counts.get(key, 0) + 1

    def count(self, a: str, b: str) -> int:
        return self.pair_counts.get(pair_key(a, b), 0)

    def degree(self, camera: str, *, min_shared: int = 1) -> int:
        """Number of cameras sharing >= ``min_shared`` co-visible groups with ``camera``.

        The anchor guard-rail (ADR-0012/0023) reads this: degree 0 means the
        anchor cannot be chained to anything.
        """
        return sum(
            1
            for other in self.cameras
            if other != camera and self.count(camera, other) >= min_shared
        )
