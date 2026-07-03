import { configureStore } from '@reduxjs/toolkit';

import camerasReducer from '@/features/cameras/camerasSlice';
import connectionReducer from '@/features/connection/connectionSlice';
import conventionReducer from '@/features/review3d/conventions';
import sessionReducer from '@/features/session/sessionSlice';
import telemetryReducer from '@/features/telemetry/telemetrySlice';

export const store = configureStore({
  reducer: {
    connection: connectionReducer,
    session: sessionReducer,
    cameras: camerasReducer,
    telemetry: telemetryReducer,
    convention: conventionReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
