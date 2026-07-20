import { Box, Center, Drawer, Flex, Loader, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useEffect, useState } from 'react';

import { useAppSelector } from '@/app/hooks';
import { Topbar } from '@/components/layout/Topbar';
import { SessionChecklist } from '@/components/SessionChecklist';
import { SettingsModal } from '@/components/SettingsModal';
import { selectSession, selectSessionStatus } from '@/features/session/sessionSlice';
import {
  selectActiveView,
  selectStages,
  type NavTarget,
  type ViewId,
} from '@/features/session/selectors';
import { StepContent } from '@/features/session/StepContent';
import { WizardRail, type RailItem } from '@/features/session/WizardRail';
import { setCaptureView } from '@/transport/httpClient';

// Top-level shell: persistent Topbar + FSM rail + scrollable main. The rail replaces
// the horizontal Stepper. `view` is volatile UI state (free navigation between
// non-locked stages); it syncs to the persisted FSM step on load/transition but
// does not itself mutate server state (ADR-0010, spec wizard-navigation).
export function WizardShell() {
  const status = useAppSelector(selectSessionStatus);
  const stages = useAppSelector(selectStages);
  const persistedView = useAppSelector(selectActiveView);
  // Load-time anomalies (ADR-0036 fail-loud): a persistent banner + a badge on
  // the concerned rail steps until the operator reconfigures them.
  const issues = useAppSelector(selectSession)?.issues ?? [];
  const alertSteps = new Set(issues.map((issue) => issue.step));

  const [view, setView] = useState<NavTarget>(persistedView);
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpened, { toggle: toggleDrawer, close: closeDrawer }] = useDisclosure(false);
  const [settingsOpened, { open: openSettings, close: closeSettings }] = useDisclosure(false);

  // Follow server-side step transitions (and the initial rehydrate landing step).
  useEffect(() => {
    setView(persistedView);
  }, [persistedView]);

  // Report the current view so the service captures only the cameras it needs
  // (ADR-0021): cameras/extrinsic → all, intrinsic → the active camera, else none.
  useEffect(() => {
    setCaptureView(view).catch(() => {});
  }, [view]);

  const items: RailItem[] = [
    { id: 'session', label: 'Session', status: 'home' },
    ...stages.map((stage) => ({
      id: stage.id,
      label: stage.label,
      status: stage.status,
      alert: alertSteps.has(stage.id),
    })),
  ];

  const navigate = (id: NavTarget) => {
    setView(id);
    closeDrawer();
  };

  const railActiveView: ViewId = view;

  return (
    <Flex direction="column" style={{ height: '100dvh', overflow: 'hidden' }}>
      <Topbar burgerOpened={drawerOpened} onBurger={toggleDrawer} onSettings={openSettings} />
      <SettingsModal opened={settingsOpened} onClose={closeSettings} />
      <SessionChecklist issues={issues} />

      <Flex style={{ flex: 1, minHeight: 0 }}>
        <Box
          visibleFrom="sm"
          style={{
            flex: '0 0 auto',
            width: collapsed ? 70 : 236,
            transition: 'width .18s ease',
            borderRight: '1px solid var(--mantine-color-dark-4)',
            overflow: 'hidden',
          }}
        >
          <WizardRail
            items={items}
            activeView={railActiveView}
            onNavigate={navigate}
            collapsed={collapsed}
            onToggleCollapse={() => setCollapsed((c) => !c)}
          />
        </Box>

        <Box style={{ flex: 1, minWidth: 0, overflowY: 'auto', background: 'var(--rc-page)' }}>
          {status === 'error' ? (
            <Center h="100%">
              <Text c="red">Failed to load the session.</Text>
            </Center>
          ) : status === 'ready' || view === 'session' ? (
            <StepContent view={view} />
          ) : (
            <Center h="100%">
              <Loader />
            </Center>
          )}
        </Box>
      </Flex>

      <Drawer
        opened={drawerOpened}
        onClose={closeDrawer}
        hiddenFrom="sm"
        position="left"
        size={256}
        withCloseButton={false}
        padding={0}
        styles={{ body: { height: '100%', padding: 0 }, content: { background: 'var(--rc-bar)' } }}
        zIndex={1000}
      >
        <WizardRail
          items={items}
          activeView={railActiveView}
          onNavigate={navigate}
          collapsed={false}
        />
      </Drawer>
    </Flex>
  );
}
