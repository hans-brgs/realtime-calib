import { describe, expect, it } from 'vitest';

import reducer, {
  type Covisibility,
  covisibilityCleared,
  covisibilityReceived,
  coverageReceived,
  type CoverageMetrics,
} from '@/features/telemetry/telemetrySlice';

const coverage: CoverageMetrics = {
  type: 'coverage_metrics',
  camera: 'cam_0',
  phase: 'extrinsic',
  board_found: true,
  board_coverage: 0.4,
  tilt_deg: 12,
  sharpness: 200,
  sharpness_ok: true,
  grid_count: 30,
};

const covisibility: Covisibility = {
  type: 'covisibility',
  phase: 'extrinsic',
  cameras: ['cam_0', 'cam_1'],
  pairs: [{ a: 'cam_0', b: 'cam_1', count: 7 }],
  board_frames: { cam_0: 9, cam_1: 8 },
  synced_groups: 12,
};

describe('telemetrySlice', () => {
  it('stores coverage per camera and covisibility globally', () => {
    let state = reducer(undefined, coverageReceived(coverage));
    state = reducer(state, covisibilityReceived(covisibility));
    expect(state.coverage.cam_0.board_coverage).toBe(0.4);
    expect(state.covisibility?.pairs[0].count).toBe(7);
    expect(state.covisibility?.synced_groups).toBe(12);
  });

  it('clears covisibility when a new sweep starts', () => {
    let state = reducer(undefined, covisibilityReceived(covisibility));
    state = reducer(state, covisibilityCleared());
    expect(state.covisibility).toBeNull();
  });
});
