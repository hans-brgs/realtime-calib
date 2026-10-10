// Pre-export checks (ADR-0057): the backend computes and judges them; this only
// names them and formats their value for the Export screen. None blocks an export.
import type { ExportCheck } from '@/transport/httpClient';

export const CHECK_LABELS: Record<string, string> = {
  camera_error: 'Per-camera error',
  epipolar: 'Epipolar consistency',
  target_rigidity: 'Target rigidity',
  frame: 'World frame',
  cameras_above_floor: 'Cameras above the floor',
  reference: 'Reference calibration',
};

// The Export screen's section caption, shared with its checks panel.
export const SECTION_LABEL = {
  fz: '0.66rem',
  fw: 600,
  c: 'dark.3',
  tt: 'uppercase',
  style: { letterSpacing: '0.07em' },
} as const;

export const STATUS_COLORS: Record<ExportCheck['status'], string> = {
  ok: 'green',
  warn: 'yellow',
  fail: 'red',
  unavailable: 'gray',
};

// The value in the unit its thresholds speak: output px, or a share of the target.
export function formatCheckValue(check: ExportCheck): string {
  if (check.value == null) return '';
  switch (check.id) {
    case 'camera_error':
      return `worst ${check.value.toFixed(2)} px`;
    case 'epipolar':
      return `worst pair ${check.value.toFixed(2)} px`;
    case 'target_rigidity':
      return `${(check.value * 100).toFixed(2)} % of its size`;
    case 'cameras_above_floor':
      return check.value > 0 ? `${check.value} below` : '';
    case 'reference':
      return `residual ${(check.value * 100).toFixed(1)} cm`;
    case 'frame': {
      // The framed target's printed face against the up axis: only a tilt is news.
      const tilt = Math.min(check.value, 180 - check.value);
      return tilt > 1 ? `${tilt.toFixed(1)}° off level` : '';
    }
    default:
      return check.value.toFixed(2);
  }
}
