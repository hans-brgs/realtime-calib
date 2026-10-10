import type { ExtrinsicResultPayload } from '@/transport/httpClient';

// Quality bands shared by the Result summary: thresholds live here, next to the
// helper that reads them, so the number the operator sees and the sentence the
// popover states can never drift apart.
export type QualityLevel = 'good' | 'watch' | 'bad';

// Per-camera deviation, in pixels AT THE OUTPUT RESOLUTION — the unit every
// reported extrinsic error now carries (ADR-0042). A bare pixel threshold means
// nothing without that reference: the same rig at 4K and at 720p would be judged
// on the same number.
//
// Anchored on what recorded hand-held sweeps achieve rather than a round figure:
// four sessions from two rigs (three single-marker, one ChArUco), solved by this
// pipeline before Minimize, give 0.10-0.76 px per camera, every one holding its
// target rigid to under 0.5 mm. "Good" covers all but one camera and "watch" sits at
// twice it. The static scatter of border-refined single-marker corners, ~0.12 px at
// a 0.5-factor output (ADR-0052), is of the order of the best cameras'; most of the
// rest is attributed to the target's residual motion between cameras. Past "watch" a
// camera is beyond every reference sweep we measured: look for a cause of its own
// (sync, focus, intrinsics).
export const DEVIATION_GOOD_PX = 0.6;
export const DEVIATION_WATCH_PX = 1.2;

// Board rigidity (ADR-0044): the declared constraint tolerance (σ = 2 mm) and its
// 2.5x. Under σ the reconstructed target is as rigid as the constraint asks; past
// 2.5σ the solve is bending a target it was explicitly told not to bend.
export const RIGIDITY_GOOD_MM = 2;
export const RIGIDITY_WATCH_MM = 5;

// Fraction of observations Minimize drops (ADR-0036, Caliscope's quality loop).
// Fixed by the pipeline, not a knob — which is why the summary reports the ratio
// without a colour code: it has no bad value to warn about.
export const REFINE_FILTER_PERCENT = 2.5;

export function qualityLevel(value: number, good: number, watch: number): QualityLevel {
  if (value <= good) return 'good';
  if (value <= watch) return 'watch';
  return 'bad';
}

export function levelColor(level: QualityLevel): string {
  return level === 'good'
    ? 'var(--rc-success)'
    : level === 'watch'
      ? 'var(--rc-warning)'
      : 'var(--rc-error)';
}

// The Minimize outlier filter as the summary reports it (ADR-0036). `dropped` is 0
// on a full solve — Minimize is what filters — so the panel can tell "never
// filtered" from "filtered" after a reload, which the transient click notice
// could not. Null when the payload predates the fields.
export interface ObservationFilter {
  used: number;
  total: number;
  dropped: number;
}

export function observationFilter(result: ExtrinsicResultPayload): ObservationFilter | null {
  const { observations_used: used, observations_total: total } = result;
  if (used == null || total == null || total <= 0) return null;
  return { used, total, dropped: Math.max(0, total - used) };
}

// Counts run to five digits on a long sweep; grouped digits stay readable in a
// 300 px panel where a bare 12480 does not.
export function formatCount(value: number): string {
  return value.toLocaleString('en-US');
}
