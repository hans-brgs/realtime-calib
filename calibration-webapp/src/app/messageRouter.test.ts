import { configureStore } from '@reduxjs/toolkit';
import { describe, expect, it } from 'vitest';

import { dataChannelMessageReceived, listenerMiddleware } from '@/app/listenerMiddleware';
import { routeDataChannelMessage } from '@/app/messageRouter';
import telemetryReducer, {
  type Covisibility,
  type CoverageMetrics,
  coverageReceived,
  covisibilityReceived,
} from '@/features/telemetry/telemetrySlice';

const coverage: CoverageMetrics = {
  type: 'coverage_metrics',
  camera: 'cam_0',
  phase: 'intrinsic',
  board_found: true,
  board_coverage: 0.52,
  tilt_deg: 18,
  sharpness: 142,
  sharpness_ok: true,
  grid_count: 18,
};

const covisibility: Covisibility = {
  type: 'covisibility',
  phase: 'extrinsic',
  cameras: ['cam_0', 'cam_1'],
  pairs: [{ a: 'cam_0', b: 'cam_1', count: 7 }],
  board_frames: { cam_0: 9, cam_1: 8 },
  synced_groups: 12,
};

describe('routeDataChannelMessage', () => {
  it('routes coverage_metrics to coverageReceived', () => {
    expect(routeDataChannelMessage(JSON.stringify(coverage))).toEqual(coverageReceived(coverage));
  });

  it('routes covisibility to covisibilityReceived', () => {
    expect(routeDataChannelMessage(JSON.stringify(covisibility))).toEqual(
      covisibilityReceived(covisibility),
    );
  });

  it('ignores an unknown type (e.g. a not-yet-routed future message)', () => {
    expect(routeDataChannelMessage(JSON.stringify({ type: 'camera_state', alive: true }))).toBeNull();
  });

  it('ignores a payload without a string type discriminant', () => {
    expect(routeDataChannelMessage(JSON.stringify({ camera: 'cam_0' }))).toBeNull();
  });

  it('ignores malformed JSON without throwing', () => {
    expect(routeDataChannelMessage('{ not json')).toBeNull();
  });
});

describe('data-channel routing (store + listener middleware)', () => {
  it('a raw dataChannelMessageReceived updates the telemetry slice, keyed by type', async () => {
    const store = configureStore({
      reducer: { telemetry: telemetryReducer },
      middleware: (getDefaultMiddleware) =>
        getDefaultMiddleware().prepend(listenerMiddleware.middleware),
    });

    store.dispatch(
      dataChannelMessageReceived({ topic: 'telemetry', text: JSON.stringify(coverage) }),
    );
    store.dispatch(
      dataChannelMessageReceived({ topic: 'telemetry', text: JSON.stringify(covisibility) }),
    );
    // Listener effects run asynchronously — let the task queue drain before asserting.
    await new Promise((resolve) => setTimeout(resolve, 0));

    const state = store.getState();
    expect(state.telemetry.coverage.cam_0.board_coverage).toBe(0.52);
    expect(state.telemetry.covisibility?.synced_groups).toBe(12);
  });
});
