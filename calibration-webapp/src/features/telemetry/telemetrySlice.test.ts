import { describe, expect, it } from 'vitest';

import reducer, {
  cameraStateReceived,
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

describe('cameraStateReceived', () => {
  it('replaces the whole rig snapshot and records when it arrived', () => {
    let state = reducer(
      undefined,
      cameraStateReceived({
        type: 'camera_state',
        cameras: {
          cam_0: { state: 'live', reason: null, for_s: 1, retry_in_s: null },
          cam_1: { state: 'error', reason: 'no frame for 3 s', for_s: 0, retry_in_s: 1 },
        },
      }),
    );
    expect(state.cameraStateAt).not.toBeNull();
    // A full snapshot, not a delta: a camera absent from the next one is gone
    // (removed from the config), not left over from the previous message.
    state = reducer(
      state,
      cameraStateReceived({
        type: 'camera_state',
        cameras: { cam_0: { state: 'live', reason: null, for_s: 2, retry_in_s: null } },
      }),
    );
    expect(Object.keys(state.cameraState)).toEqual(['cam_0']);
  });
});
