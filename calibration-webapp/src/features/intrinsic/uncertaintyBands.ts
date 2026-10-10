// Projection uncertainty of an intrinsic solve (ADR-0055): how far the solved model
// could misplace a pixel's ray, 1 sigma, in pixels at the export resolution, the unit
// of every reported intrinsic error. Discrete bands so the operator reads levels, not
// a gradient. Anchored on 12 recorded 1080p cameras at a 0.5-factor output: where the
// board went, 0.35-2.3 px; where it never went, 0.9-3.2 px. Past the lens model's
// distortion fold no ray reaches the pixel: those cells carry no figure (null).
export interface UncertaintyBand {
  max: number; // upper bound of the band (px), inclusive
  label: string;
  rgb: readonly [number, number, number];
  alpha: number;
}

// The theme's success / warning / error tokens (index.css), plus the coverage gauge's
// orange: a canvas reads RGB, not CSS variables.
const SUCCESS = [52, 211, 153] as const;
const WARNING = [251, 191, 36] as const;
const ORANGE = [251, 146, 60] as const;
const ERROR = [248, 113, 113] as const;

export const UNCERTAINTY_BANDS: readonly UncertaintyBand[] = [
  { max: 0.5, label: '≤ 0.5', rgb: SUCCESS, alpha: 0.55 },
  { max: 1, label: '≤ 1', rgb: SUCCESS, alpha: 0.28 },
  { max: 2, label: '≤ 2', rgb: WARNING, alpha: 0.5 },
  { max: 5, label: '≤ 5', rgb: ORANGE, alpha: 0.6 },
  { max: Number.POSITIVE_INFINITY, label: '> 5 px', rgb: ERROR, alpha: 0.75 },
];

export function uncertaintyBand(px: number): UncertaintyBand {
  return (
    UNCERTAINTY_BANDS.find((band) => px <= band.max) ??
    UNCERTAINTY_BANDS[UNCERTAINTY_BANDS.length - 1]
  );
}

// "0.6 / 4.2 px" (covered / elsewhere), a dash for a side the solve could not read.
export function formatUncertainty(covered?: number | null, uncovered?: number | null): string {
  if (covered == null && uncovered == null) return '—';
  const show = (value?: number | null) => (value == null ? '—' : capUncertainty(value));
  return `${show(covered)} / ${show(uncovered)} px`;
}

// The paint of a cell outside the lens model: neutral, it is no level of the scale.
export const OUTSIDE_MODEL = { label: 'outside model', rgb: [113, 113, 122], alpha: 0.35 } as const;

// Huge values (a nearly degenerate solve) say "unusable", not a figure to read.
export function capUncertainty(px: number): string {
  return px > 100 ? '> 100' : px.toFixed(1);
}
