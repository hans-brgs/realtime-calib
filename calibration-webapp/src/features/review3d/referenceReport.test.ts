import { describe, expect, it } from 'vitest';

import type { AlignmentReport } from '@/transport/httpClient';

import { defaultMode, reportRows, residualRows } from './referenceReport';

const floor: AlignmentReport = {
  mode: 'floor',
  refused: null,
  matched_by: 'device_path',
  cameras: ['cam_0', 'cam_1', 'cam_2', 'cam_3'],
  rotation_deg: 2.134,
  translation_m: [0.2, 0, -0.051],
  residual_rms_m: 0.0581,
  residuals_m: {},
  vertical_offset_m: 0.031,
  tilt_deg: 1.49,
  angle_sigma_deg: null,
  scale_ratio: 1.0095,
};

describe('reportRows', () => {
  it('says what the floor mode leaves out', () => {
    expect(reportRows(floor)).toEqual([
      ['Matched by', 'device path · 4 cameras'],
      ['Rotation', '2.13°'],
      ['Translation', '0.200, 0.000, -0.051 m'],
      ['Residual', '5.8 cm RMS'],
      ['Height offset (not applied)', '3.1 cm'],
      ['Floor tilt (not applied)', '1.49°'],
      ['Scale ratio (not applied)', '1.0095'],
    ]);
  });

  it('flags a match by port', () => {
    const rows = reportRows({ ...floor, mode: 'rigid', matched_by: 'port', angle_sigma_deg: 0.7 });
    expect(rows[0]).toEqual([
      'Matched by',
      'port (imported session or no device paths) · 4 cameras',
    ]);
    expect(rows).toContainEqual(['Angle uncertainty', '±0.70°']);
  });
});

describe('residualRows', () => {
  it('lists each camera in centimetres, by name', () => {
    const report = { ...floor, residuals_m: { cam_1: 0.0423, cam_0: 0.1287 } };
    expect(residualRows(report)).toEqual([
      ['cam_0', '12.9 cm'],
      ['cam_1', '4.2 cm'],
    ]);
  });
});

describe('defaultMode', () => {
  it('offers floor when it applies, rigid otherwise', () => {
    expect(defaultMode({ floor, rigid: { ...floor, mode: 'rigid' } })).toBe('floor');
    expect(defaultMode({ floor: { ...floor, refused: 'not framed' } })).toBe('rigid');
    expect(defaultMode({})).toBe('rigid');
  });
});
