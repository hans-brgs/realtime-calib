import { describe, expect, it } from 'vitest';

import { failingCameras, freshCameraStates } from '@/features/preview/useCameraHealth';
import {
  CAMERA_STATE_STALE_MS,
  type CameraCaptureState,
} from '@/features/telemetry/telemetrySlice';

const live: CameraCaptureState = { state: 'live', reason: null, for_s: 3, retry_in_s: null };
const lost: CameraCaptureState = {
  state: 'error',
  reason: 'no frame for 3 s',
  for_s: 1,
  retry_in_s: 1,
};

describe('freshCameraStates', () => {
  it('trusts a recent snapshot', () => {
    const states = { cam_0: live };
    expect(freshCameraStates(states, 1000, 1000 + CAMERA_STATE_STALE_MS)).toBe(states);
  });

  it('drops a snapshot the service stopped refreshing', () => {
    // The service re-sends every second: silence past the cut-off means it is gone,
    // and an error it last reported may well be fixed — do not keep showing it.
    expect(freshCameraStates({ cam_1: lost }, 1000, 1001 + CAMERA_STATE_STALE_MS)).toEqual({});
  });

  it('knows nothing before the first snapshot', () => {
    expect(freshCameraStates({}, null, 5000)).toEqual({});
  });
});

describe('failingCameras', () => {
  it('lists only cameras in error, in natural track order', () => {
    const failing = failingCameras({ cam_10: lost, cam_0: live, cam_2: lost });
    expect(failing.map(([name]) => name)).toEqual(['cam_2', 'cam_10']);
  });
});
