// Pipeline defaults and bounds served by the backend (GET /defaults, ADR-0036).
// This is the webapp's single source for knob seeds and NumberInput min/max —
// no hardcoded copies. Loaded once at app mount, alongside the session
// rehydration; on a failed load the value stays null and screens keep whatever
// they last seeded (inputs remain usable, just not re-seeded).

import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';

import { fetchDefaults } from '@/transport/httpClient';
import type { PipelineDefaults } from '@/transport/types';

interface DefaultsState {
  value: PipelineDefaults | null;
}

const initialState: DefaultsState = { value: null };

export const loadDefaults = createAsyncThunk('defaults/load', () => fetchDefaults());

const defaultsSlice = createSlice({
  name: 'defaults',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder.addCase(loadDefaults.fulfilled, (state, action) => {
      state.value = action.payload;
    });
  },
});

export const selectDefaults = (state: { defaults: DefaultsState }): PipelineDefaults | null =>
  state.defaults.value;

export default defaultsSlice.reducer;
