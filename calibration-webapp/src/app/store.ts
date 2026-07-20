import { configureStore } from '@reduxjs/toolkit';

import { listenerMiddleware } from '@/app/listenerMiddleware';
import camerasReducer from '@/features/cameras/camerasSlice';
import connectionReducer from '@/features/connection/connectionSlice';
import defaultsReducer from '@/features/session/defaultsSlice';
import sessionReducer from '@/features/session/sessionSlice';
import telemetryReducer from '@/features/telemetry/telemetrySlice';

export const store = configureStore({
  reducer: {
    connection: connectionReducer,
    session: sessionReducer,
    defaults: defaultsReducer,
    cameras: camerasReducer,
    telemetry: telemetryReducer,
  },
  // Data-channel telemetry is routed by the listener middleware (spec realtime-telemetry);
  // prepend it so it sees inbound actions before the default middleware.
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware().prepend(listenerMiddleware.middleware),
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
