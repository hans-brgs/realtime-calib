import '@mantine/core/styles.css';
import '@livekit/components-styles';

import { MantineProvider } from '@mantine/core';
import { Provider } from 'react-redux';

import { store } from '@/app/store';
import { AppLayout } from '@/components/layout/AppLayout';
import { PreviewGrid } from '@/features/preview/PreviewGrid';
import { theme } from '@/theme';

// Dark mode is the default (ADR-0010 / vision-webapp design system).
export default function App() {
  return (
    <MantineProvider theme={theme} defaultColorScheme="dark">
      <Provider store={store}>
        <AppLayout>
          <PreviewGrid />
        </AppLayout>
      </Provider>
    </MantineProvider>
  );
}
