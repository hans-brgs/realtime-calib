import { configureStore } from '@reduxjs/toolkit';

import camerasReducer from '@/features/cameras/camerasSlice';
import connectionReducer from '@/features/connection/connectionSlice';
import sessionReducer from '@/features/session/sessionSlice';
import telemetryReducer from '@/features/telemetry/telemetrySlice';

export const store = configureStore({
  reducer: {
    connection: connectionReducer,
    session: sessionReducer,
    cameras: camerasReducer,
    telemetry: telemetryReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
