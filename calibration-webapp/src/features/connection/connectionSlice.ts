import { createSlice } from '@reduxjs/toolkit';

import type { RootState } from '@/app/store';

export type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'disconnected';

interface ConnectionState {
  status: ConnectionStatus;
}

const initialState: ConnectionState = {
  status: 'idle',
};

const connectionSlice = createSlice({
  name: 'connection',
  initialState,
  reducers: {
    connecting(state) {
      state.status = 'connecting';
    },
    connectionEstablished(state) {
      state.status = 'connected';
    },
    connectionLost(state) {
      state.status = 'disconnected';
    },
  },
});

export const { connecting, connectionEstablished, connectionLost } = connectionSlice.actions;

export const selectConnectionStatus = (state: RootState): ConnectionStatus =>
  state.connection.status;

export default connectionSlice.reducer;
