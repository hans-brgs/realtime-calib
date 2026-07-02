import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import type { RootState } from '@/app/store';
import {
  computeIntrinsic,
  configureCameras,
  defineBoard,
  fetchSession,
  fetchSessions,
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

export const applyBoardConfig = createAsyncThunk('session/applyBoard', (request: BoardConfigRequest) =>
  defineBoard(request),
);

export const computeIntrinsicThunk = createAsyncThunk('session/computeIntrinsic', (camera: string) =>
  computeIntrinsic(camera),
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
      .addCase(applyBoardConfig.fulfilled, (state, action) => {
        state.session = action.payload;
      })
      .addCase(computeIntrinsicThunk.fulfilled, (state, action) => {
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
