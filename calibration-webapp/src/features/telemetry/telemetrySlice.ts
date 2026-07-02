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

interface TelemetryState {
  coverage: Record<string, CoverageMetrics>;
}

const initialState: TelemetryState = { coverage: {} };

const telemetrySlice = createSlice({
  name: 'telemetry',
  initialState,
  reducers: {
    coverageReceived(state, action: PayloadAction<CoverageMetrics>) {
      state.coverage[action.payload.camera] = action.payload;
    },
  },
});

export const { coverageReceived } = telemetrySlice.actions;
export default telemetrySlice.reducer;

export const selectCoverage =
  (camera: string | null) =>
  (state: RootState): CoverageMetrics | null =>
    camera ? (state.telemetry.coverage[camera] ?? null) : null;
