import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import type { RootState } from '@/app/store';
import { detectCameras } from '@/transport/httpClient';
import type { DetectedCamera } from '@/transport/types';

type Status = 'idle' | 'loading' | 'ready' | 'error';

interface CamerasState {
  detected: DetectedCamera[];
  status: Status;
  error: string | null;
}

const initialState: CamerasState = {
  detected: [],
  status: 'idle',
  error: null,
};

export const detectCamerasThunk = createAsyncThunk('cameras/detect', () => detectCameras());

const camerasSlice = createSlice({
  name: 'cameras',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(detectCamerasThunk.pending, (state) => {
        state.status = 'loading';
        state.error = null;
      })
      .addCase(detectCamerasThunk.fulfilled, (state, action) => {
        state.status = 'ready';
        state.detected = action.payload;
      })
      .addCase(detectCamerasThunk.rejected, (state, action) => {
        state.status = 'error';
        state.error = action.error.message ?? 'failed';
      });
  },
});

export default camerasSlice.reducer;

export const selectDetectedCameras = (state: RootState): DetectedCamera[] => state.cameras.detected;
export const selectDetectStatus = (state: RootState): Status => state.cameras.status;
