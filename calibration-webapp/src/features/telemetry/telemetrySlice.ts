import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

import type { RootState } from '@/app/store';

// Aggregate coverage metrics pushed on the LiveKit data channel (coverage-metrics
// entity). Keyed by camera track name. Volatile — never persisted.
export interface CoverageMetrics {
  type: 'coverage_metrics';
  camera: string;
  phase: string;
  board_found: boolean;
  board_coverage: number;
  tilt_deg: number | null;
  sharpness: number;
  sharpness_ok: boolean;
  grid_count: number;
}

// Pairwise co-visibility pushed during the synchronized extrinsic sweep (ADR-0007/
// 0023): per-pair joint board views, per-camera detection tallies, group count.
export interface CovisibilityPair {
  a: string;
  b: string;
  count: number;
}

export interface Covisibility {
  type: 'covisibility';
  phase: string;
  cameras: string[];
  pairs: CovisibilityPair[];
  board_frames: Record<string, number>;
  synced_groups: number;
}

// Live capture health of the whole rig (spec realtime-telemetry, #46): a full
// snapshot re-sent every reconcile tick (>= 1 Hz) on the lossy telemetry topic.
// `idle` = closed on purpose for the current view; `error` carries the reason.
export type CaptureState = 'live' | 'opening' | 'error' | 'idle';

export interface CameraCaptureState {
  state: CaptureState;
  reason: string | null;
  for_s: number;
  retry_in_s: number | null;
}

export interface CameraStateMessage {
  type: 'camera_state';
  cameras: Record<string, CameraCaptureState>;
}

interface TelemetryState {
  coverage: Record<string, CoverageMetrics>;
  covisibility: Covisibility | null;
  cameraState: Record<string, CameraCaptureState>;
  // Local receipt time (ms epoch) of the last snapshot: when the service stops
  // sending (room drop, restart), the last one must not pass for current.
  cameraStateAt: number | null;
}

const initialState: TelemetryState = {
  coverage: {},
  covisibility: null,
  cameraState: {},
  cameraStateAt: null,
};

const telemetrySlice = createSlice({
  name: 'telemetry',
  initialState,
  reducers: {
    coverageReceived(state, action: PayloadAction<CoverageMetrics>) {
      state.coverage[action.payload.camera] = action.payload;
    },
    covisibilityReceived(state, action: PayloadAction<Covisibility>) {
      state.covisibility = action.payload;
    },
    covisibilityCleared(state) {
      state.covisibility = null;
    },
    cameraStateReceived: {
      // Replaced wholesale: each message is the full rig, not a delta.
      reducer(state, action: PayloadAction<CameraStateMessage & { receivedAt: number }>) {
        state.cameraState = action.payload.cameras;
        state.cameraStateAt = action.payload.receivedAt;
      },
      // The receipt clock is read here, not in the reducer, which must stay pure.
      prepare(message: CameraStateMessage) {
        return { payload: { ...message, receivedAt: Date.now() } };
      },
    },
  },
});

export const { coverageReceived, covisibilityReceived, covisibilityCleared, cameraStateReceived } =
  telemetrySlice.actions;
export default telemetrySlice.reducer;

export const selectCoverage =
  (camera: string | null) =>
  (state: RootState): CoverageMetrics | null =>
    camera ? (state.telemetry.coverage[camera] ?? null) : null;

export const selectCovisibility = (state: RootState): Covisibility | null =>
  state.telemetry.covisibility;

// A snapshot older than this is no longer evidence of anything: the service sends one
// at least every second, so 5 s of silence means it stopped (room drop, restart).
export const CAMERA_STATE_STALE_MS = 5000;

export const selectCameraStates = (state: RootState): Record<string, CameraCaptureState> =>
  state.telemetry.cameraState;

export const selectCameraStateAt = (state: RootState): number | null =>
  state.telemetry.cameraStateAt;
