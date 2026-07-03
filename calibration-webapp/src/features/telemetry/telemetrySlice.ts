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

interface TelemetryState {
  coverage: Record<string, CoverageMetrics>;
  covisibility: Covisibility | null;
}

const initialState: TelemetryState = { coverage: {}, covisibility: null };

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
  },
});

export const { coverageReceived, covisibilityReceived, covisibilityCleared } =
  telemetrySlice.actions;
export default telemetrySlice.reducer;

export const selectCoverage =
  (camera: string | null) =>
  (state: RootState): CoverageMetrics | null =>
    camera ? (state.telemetry.coverage[camera] ?? null) : null;

export const selectCovisibility = (state: RootState): Covisibility | null =>
  state.telemetry.covisibility;
