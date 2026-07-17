"""Single source of truth for user-facing pipeline defaults and caps (ADR-0036).

Admission criterion: a value belongs here only if a user setting reads it as its
default, or is bounded by it. Solver internals, Caliscope-parity invariants and
structural minimums stay as module constants next to their point of use.

Propagation is one-way: backend -> UI. ``GET /defaults`` serves this object to
the webapp, which seeds its inputs from it instead of hardcoding copies; request
models default to ``None`` and resolve against it in the transport layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BoardDefaults:
    """Default board definition served to the webapp (Target Config form).

    Canonical values: the ones the webapp effectively applied before ADR-0036
    (its form always sent every field). ChArUco-only and ArUco-only fields
    coexist; consumers read the subset their board type uses.
    """

    board_type: str = "charuco"
    dictionary: str = "DICT_4X4_100"
    columns: int = 7
    rows: int = 9
    marker_ratio: float = 0.75
    square_size_mm: float = 40.0
    marker_size_mm: float = 30.0
    marker_id: int = 0
    inverted: bool = False


@dataclass(frozen=True)
class PipelineTuning:
    """User-facing defaults and bounds of the vision pipeline (ADR-0036...0039)."""

    # --- Capture (ADR-0037: one cadence per camera; the ladder max IS the cap) ---
    fps_options: tuple[int, ...] = (30, 15)
    default_fps: int = 30  # pre-configuration preview + webapp seed fallback
    # JPEG quality of recorded mkvs — the pixels every offline compute re-detects.
    record_quality: int = 95
    record_quality_bounds: tuple[int, int] = (85, 100)
    # LiveKit publication rate. None = follow the camera fps (full fidelity);
    # a lower value only spares the publish chain (downscale/burn-in/RGBA/VP8) —
    # recording, live detection grids and compute are never affected.
    preview_fps: int | None = None
    preview_fps_options: tuple[int, ...] = (15,)
    # Output-contract scale factors (ADR-0015): calibration always runs native;
    # s only rescales the exported K and size. Closed list keeps dimensions even.
    resize_factors: tuple[float, ...] = (1.0, 0.75, 0.5, 1 / 3, 0.25)

    # --- Intrinsic Prepare (ADR-0022/0038) ---
    intrinsic_stride: int = 5  # decode+detect 1 frame every N within the trim
    intrinsic_stride_bounds: tuple[int, int] = (1, 30)
    # Keyframes kept for calibrateCamera. 50, not 25, on MEASURED evidence
    # (ADR-0038 A/B on sessions/test, 4 identical cameras): at 25 the four focal
    # estimates scatter ~10 px (0.74%) and depend on which frames the selector
    # happened to pick; at 50 they settle to ~4-6 px AND two different selectors
    # agree within 0.4% — i.e. 25 samples the sweep, 50 measures the lens.
    # Cost is a few seconds per camera (detection, not the solve, dominates).
    intrinsic_cap: int = 50
    intrinsic_cap_bounds: tuple[int, int] = (6, 100)

    # --- Extrinsic Prepare (ADR-0023/0033/0036) ---
    # Detection stride over the spread-filtered candidate groups ("1 group / N").
    # Per-board defaults mirror the detection cost gap (ChArUco ~10x a marker).
    extrinsic_stride_charuco: int = 12
    extrinsic_stride_marker: int = 2
    extrinsic_stride_bounds: tuple[int, int] = (1, 30)
    # Sharpest groups kept for the solve, per board type (a marker view carries
    # only 4 corners, so markers get a larger keep budget).
    max_groups_charuco: int = 80
    max_groups_marker: int = 240
    max_groups_bounds: tuple[int, int] = (5, 960)
    max_spread_ms_bounds: tuple[float, float] = (1.0, 100.0)
    # Minimum shared board views per camera pair (API-only since ADR-0036; the
    # UI control was removed — possible reintegration later under an Advanced
    # section if the rescue scenario proves common).
    min_shared: int = 5
    min_shared_bounds: tuple[int, int] = (2, 30)

    # --- Board & export ---
    board: BoardDefaults = field(default_factory=BoardDefaults)
    # Caliscope-native unit (metres); per-session preference persists via
    # /export/config, this only seeds sessions that never chose.
    export_units: str = "m"
    export_units_options: tuple[str, ...] = ("mm", "m")


TUNING = PipelineTuning()
