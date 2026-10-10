// Re-alignment report (ADR-0061): what one mode's fit would do, as label/value rows.
// The backend judges; this only puts its numbers in the units an operator reads.
import type { AlignmentReport } from '@/transport/httpClient';

const cm = (metres: number) => `${(metres * 100).toFixed(1)} cm`;

export function reportRows(report: AlignmentReport): [string, string][] {
  const rows: [string, string][] = [];
  if (report.matched_by) {
    const key =
      report.matched_by === 'port' ? 'port (imported session or no device paths)' : 'device path';
    rows.push(['Matched by', `${key} · ${report.cameras.length} cameras`]);
  }
  if (report.rotation_deg != null) rows.push(['Rotation', `${report.rotation_deg.toFixed(2)}°`]);
  if (report.translation_m) {
    const [x, y, z] = report.translation_m;
    rows.push(['Translation', `${[x, y, z].map((v) => v.toFixed(3)).join(', ')} m`]);
  }
  if (report.residual_rms_m != null) rows.push(['Residual', `${cm(report.residual_rms_m)} RMS`]);
  if (report.mode === 'floor') {
    if (report.vertical_offset_m != null) {
      rows.push(['Height offset (not applied)', cm(report.vertical_offset_m)]);
    }
    if (report.tilt_deg != null) {
      rows.push(['Floor tilt (not applied)', `${report.tilt_deg.toFixed(2)}°`]);
    }
  } else {
    if (report.tilt_deg != null) rows.push(['Floor tilt', `${report.tilt_deg.toFixed(2)}°`]);
    if (report.angle_sigma_deg != null) {
      rows.push(['Angle uncertainty', `±${report.angle_sigma_deg.toFixed(2)}°`]);
    }
  }
  if (report.scale_ratio != null) {
    rows.push(['Scale ratio (not applied)', report.scale_ratio.toFixed(4)]);
  }
  return rows;
}

// Per camera, how far its centre lands from the reference's: which one most likely moved.
export function residualRows(report: AlignmentReport): [string, string][] {
  return Object.entries(report.residuals_m)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, metres]) => [name, cm(metres)]);
}

// The mode to offer first: floor when it can apply, else rigid.
export function defaultMode(preview: Partial<Record<'floor' | 'rigid', AlignmentReport>>) {
  return preview.floor && preview.floor.refused == null ? 'floor' : 'rigid';
}
