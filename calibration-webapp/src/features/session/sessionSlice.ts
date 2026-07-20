import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import type { RootState } from '@/app/store';
import {
  type ComputeParams,
  computeExtrinsic,
  computeIntrinsic,
  configureCameras,
  confirmCameraSetup,
  createSession,
  defineBoard,
  type ExtrinsicComputeParams,
  fetchSession,
  fetchSessions,
  importSession,
  openSession,
  validateExtrinsic,
  validateIntrinsic,
} from '@/transport/httpClient';
import type { BoardConfigRequest, ConfigRequest, Session, SessionSummary } from '@/transport/types';

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

// Rehydrate from disk-owned state at mount (ADR-0011): no localStorage. Resolves to
// null when no session is active (ADR-0028) — the shell shows the dashboard.
export const rehydrateSession = createAsyncThunk('session/rehydrate', () => fetchSession());

export const fetchRecentSessions = createAsyncThunk('session/recent', () => fetchSessions());

// Create a new session (unique folder name) and make it active (ADR-0028).
export const createSessionThunk = createAsyncThunk('session/create', (sessionId: string) =>
  createSession(sessionId),
);

// Switch the active session to an existing one (Recent sessions / resume).
export const openSessionThunk = createAsyncThunk('session/open', (sessionId: string) =>
  openSession(sessionId),
);

// Import a pre-recorded session ZIP (ADR-0031): the service ingests it and makes
// it active; the rail then follows the persisted step to Target Config.
export const importSessionThunk = createAsyncThunk(
  'session/import',
  ({ file, sessionId }: { file: File; sessionId: string }) => importSession(file, sessionId),
);

// Load-from-files "Continue" on Camera Setup: unlocks Intrinsics without
// rebuilding the camera configs (they derive from the imported videos).
export const confirmCameraSetupThunk = createAsyncThunk('session/confirmCameras', () =>
  confirmCameraSetup(),
);

export const applyCameraConfig = createAsyncThunk('session/applyConfig', (request: ConfigRequest) =>
  configureCameras(request),
);

// Intrinsic sign-off: the persisted step moves to 'extrinsic_capture' and the wizard follows.
export const validateIntrinsicThunk = createAsyncThunk('session/validateIntrinsic', () =>
  validateIntrinsic(),
);

// Extrinsic sign-off: the persisted step moves to 'export' and the wizard follows.
export const validateExtrinsicThunk = createAsyncThunk('session/validateExtrinsic', () =>
  validateExtrinsic(),
);

export const applyBoardConfig = createAsyncThunk(
  'session/applyBoard',
  (request: BoardConfigRequest) => defineBoard(request),
);

export const computeIntrinsicThunk = createAsyncThunk(
  'session/computeIntrinsic',
  ({ camera, params }: { camera: string; params?: ComputeParams }) =>
    computeIntrinsic(camera, params),
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
      .addCase(createSessionThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(openSessionThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(importSessionThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(confirmCameraSetupThunk.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(applyCameraConfig.fulfilled, (state, action) => {
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
export const selectRecentSessions = (state: RootState): SessionSummary[] => state.session.recent;
