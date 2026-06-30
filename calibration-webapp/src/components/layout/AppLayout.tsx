import { AppShell, Group, Title } from '@mantine/core';
import type { ReactNode } from 'react';

interface AppLayoutProps {
  children: ReactNode;
}

export function AppLayout({ children }: AppLayoutProps) {
  return (
    <AppShell header={{ height: 56 }} padding="xs">
      <AppShell.Header>
        <Group h="100%" px="md">
          <Title order={4}>realtime-calib</Title>
        </Group>
      </AppShell.Header>
      <AppShell.Main>{children}</AppShell.Main>
    </AppShell>
  );
}
