import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import type { RootState } from '@/app/store';
import {
  type ComputeParams,
  computeExtrinsic,
  computeIntrinsic,
  configureCameras,
  defineBoard,
  type ExtrinsicComputeParams,
  fetchSession,
  fetchSessions,
  reorderCameras,
  validateExtrinsic,
  validateIntrinsic,
} from '@/transport/httpClient';
import type {
  BoardConfigRequest,
  ConfigRequest,
  Session,
  SessionSummary,
  WizardStep,
} from '@/transport/types';

type Status = 'idle' | 'loading' | 'ready' | 'error';

interface SessionState {
  session: Session | null;
  status: Status;
  error: string | null;
  recent: SessionSummary[];
}

const initialState: SessionState = {
  session: null,
  status: 'idle',
  error: null,
  recent: [],
};

// Rehydrate from disk-owned state at mount (ADR-0011): no localStorage.
export const rehydrateSession = createAsyncThunk('session/rehydrate', () => fetchSession());

export const fetchRecentSessions = createAsyncThunk('session/recent', () => fetchSessions());

export const applyCameraConfig = createAsyncThunk('session/applyConfig', (request: ConfigRequest) =>
  configureCameras(request),
);

// Drag-reorder persistence (index = position, anchor = 0): unlike applyCameraConfig
// this keeps calibrations — the backend only permutes index + position-based name.
export const reorderCamerasThunk = createAsyncThunk(
  'session/reorderCameras',
  (devicePaths: string[]) => reorderCameras(devicePaths),
);

// Intrinsic sign-off: the persisted step moves to 'extrinsic_capture' and the wizard follows.
export const validateIntrinsicThunk = createAsyncThunk('session/validateIntrinsic', () =>
  validateIntrinsic(),
);

// Extrinsic sign-off: the persisted step moves to 'export' and the wizard follows.
export const validateExtrinsicThunk = createAsyncThunk('session/validateExtrinsic', () =>
  validateExtrinsic(),
);

export const applyBoardConfig = createAsyncThunk('session/applyBoard', (request: BoardConfigRequest) =>
  defineBoard(request),
);

export const computeIntrinsicThunk = createAsyncThunk(
  'session/computeIntrinsic',
  ({ camera, params }: { camera: string; params?: ComputeParams }) => computeIntrinsic(camera, params),
);

export const computeExtrinsicThunk = createAsyncThunk(
  'session/computeExtrinsic',
  (params: ExtrinsicComputeParams | undefined) => computeExtrinsic(params),
);

const sessionSlice = createSlice({
  name: 'session',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(rehydrateSession.pending, (state) => {
        state.status = 'loading';
        state.error = null;
      })
      .addCase(rehydrateSession.fulfilled, (state, action) => {
        state.status = 'ready';
        state.session = action.payload;
      })
      .addCase(rehydrateSession.rejected, (state, action) => {
        state.status = 'error';
        state.error = action.error.message ?? 'failed';
      })
      .addCase(applyCameraConfig.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(reorderCamerasThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(validateIntrinsicThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(validateExtrinsicThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(applyBoardConfig.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(computeIntrinsicThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(computeExtrinsicThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(fetchRecentSessions.fulfilled, (state, action) => {
        state.recent = action.payload;
      });
  },
});

export default sessionSlice.reducer;

export const selectSession = (state: RootState): Session | null => state.session.session;
export const selectSessionStatus = (state: RootState): Status => state.session.status;
export const selectStep = (state: RootState): WizardStep => state.session.session?.step ?? 'entry';
export const selectRecentSessions = (state: RootState): SessionSummary[] => state.session.recent;
