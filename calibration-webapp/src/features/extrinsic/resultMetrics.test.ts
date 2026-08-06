import { describe, expect, it } from 'vitest';

import {
  DEVIATION_GOOD_PX,
  DEVIATION_WATCH_PX,
  observationFilter,
  qualityLevel,
  RIGIDITY_GOOD_MM,
  RIGIDITY_WATCH_MM,
} from './resultMetrics';
import type { ExtrinsicResultPayload } from '@/transport/httpClient';

function result(over: Partial<ExtrinsicResultPayload> = {}): ExtrinsicResultPayload {
  return {
    cameras: ['cam_0', 'cam_1'],
    rotations: {},
    translations: {},
    per_camera_error: {},
    error: 1.0,
    pair_errors: {},
    group_count: 12,
    point_count: 48,
    points: [],
    point_groups: [],
    board_quads: [],
    ...over,
  };
}

describe('qualityLevel', () => {
  it('reads a threshold as inclusive, so the popover text matches the colour', () => {
    // The popover states "≤ 2 mm nominal". A value sitting exactly on a bound
    // must therefore take the BETTER band, or the sentence would lie about the
    // colour the operator is looking at.
    expect(qualityLevel(RIGIDITY_GOOD_MM, RIGIDITY_GOOD_MM, RIGIDITY_WATCH_MM)).toBe('good');
    expect(qualityLevel(RIGIDITY_WATCH_MM, RIGIDITY_GOOD_MM, RIGIDITY_WATCH_MM)).toBe('watch');
    expect(qualityLevel(RIGIDITY_WATCH_MM + 0.01, RIGIDITY_GOOD_MM, RIGIDITY_WATCH_MM)).toBe('bad');
  });

  it('bands a per-camera deviation on the detection-floor thresholds', () => {
    expect(qualityLevel(0.4, DEVIATION_GOOD_PX, DEVIATION_WATCH_PX)).toBe('good');
    expect(qualityLevel(0.9, DEVIATION_GOOD_PX, DEVIATION_WATCH_PX)).toBe('watch');
    expect(qualityLevel(2.5, DEVIATION_GOOD_PX, DEVIATION_WATCH_PX)).toBe('bad');
  });
});

describe('observationFilter', () => {
  it('reports a full solve as unfiltered rather than hiding the count', () => {
    // A fresh compute uses every observation, so used === total. The row still
    // earns its place: it is the size of the evidence the solve stood on.
    const filter = observationFilter(result({ observations_used: 480, observations_total: 480 }));
    expect(filter).toEqual({ used: 480, total: 480, dropped: 0 });
  });

  it('reports what Minimize dropped, which survives a reload', () => {
    const filter = observationFilter(result({ observations_used: 468, observations_total: 480 }));
    expect(filter).toEqual({ used: 468, total: 480, dropped: 12 });
  });

  it('stays silent on payloads persisted before the fields existed (ADR-0036)', () => {
    // Absent fields must not render as "0 / 0" — an old result simply does not
    // know its observation count.
    expect(observationFilter(result())).toBeNull();
    expect(observationFilter(result({ observations_used: 480 }))).toBeNull();
    expect(observationFilter(result({ observations_used: 0, observations_total: 0 }))).toBeNull();
  });
});
