import '@mantine/core/styles.css';
import '@mantine/code-highlight/styles.css'; // after core styles (Export preview)
import '@livekit/components-styles';

import { MantineProvider } from '@mantine/core';
import { useEffect } from 'react';
import { Provider } from 'react-redux';

import { useAppDispatch } from '@/app/hooks';
import { store } from '@/app/store';
import { DataChannelListener } from '@/features/connection/DataChannelListener';
import { RoomProvider } from '@/features/connection/RoomProvider';
import { rehydrateSession } from '@/features/session/sessionSlice';
import { WizardShell } from '@/features/session/WizardShell';
import { theme } from '@/theme';

// Rehydrate the wizard from the disk-owned session at mount (ADR-0011). The
// LiveKit room lives HERE, above the wizard, so navigating between steps never
// tears down the WebRTC session (see RoomProvider); the single data-channel
// subscription (DataChannelListener) is mounted here too so telemetry routing
// survives navigation (spec realtime-telemetry).
function AppContent() {
  const dispatch = useAppDispatch();
  useEffect(() => {
    dispatch(rehydrateSession());
  }, [dispatch]);
  return (
    <RoomProvider>
      <DataChannelListener />
      <WizardShell />
    </RoomProvider>
  );
}

// Dark mode is the default (ADR-0010 / vision-webapp design system).
export default function App() {
  return (
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Provider store={store}>
        <AppContent />
      </Provider>
    </MantineProvider>
  );
}
