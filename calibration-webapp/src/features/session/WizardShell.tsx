import { Box, Center, Drawer, Flex, Loader, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useEffect, useState } from 'react';

import { useAppSelector } from '@/app/hooks';
import { Topbar } from '@/components/layout/Topbar';
import { useCompactLayout } from '@/components/layout/useCompactLayout';
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

  // Portrait or narrower than `md`: the layout flows and the page scrolls; else it
  // locks to one viewport (ADR-0041). This one boolean drives the whole contract.
  const compact = useCompactLayout();

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
    <Flex
      direction="column"
      style={
        compact
          ? // Flow regime: floor at one viewport so short screens still cover it,
            // but grow with the content and let the DOCUMENT scroll.
            { minHeight: '100dvh' }
          : // Locked regime: exactly one viewport, no page scroll (the "app feel").
            { height: '100dvh', overflow: 'hidden' }
      }
    >
      <Topbar burgerOpened={drawerOpened} onBurger={toggleDrawer} onSettings={openSettings} />
      <SettingsModal opened={settingsOpened} onClose={closeSettings} />
      <SessionChecklist issues={issues} />

      <Flex style={{ flex: 1, minHeight: 0 }}>
        {/* Column only in the locked regime. In `compact` the rail would cost 236px
            of width (29% of a portrait tablet) AND scroll out of view with the
            page — it becomes a full-page overlay instead (ADR-0041). Rendered
            conditionally rather than via `visibleFrom`, so the hook stays the one
            source of truth instead of a JS/CSS split. */}
        {!compact && (
          <Box
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
        )}

        <Box
          style={{
            flex: 1,
            minWidth: 0,
            // Locked: this box IS the scroll container. Flow: it must not scroll on
            // its own — the document does, otherwise content past the fold is
            // trapped inside a box that was never given room to scroll.
            overflowY: compact ? 'visible' : 'auto',
            background: 'var(--rc-page)',
          }}
        >
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

      {/* Full-page overlay, not a 256px panel: at full width there is no "outside"
          left to tap, so the close button is mandatory here — without it a locked
          stage (unclickable) would leave no way out but Escape. */}
      {compact && (
        <Drawer
          opened={drawerOpened}
          onClose={closeDrawer}
          position="left"
          size="100%"
          withCloseButton
          closeButtonProps={{
            // xl = 44px exactly; lg would be 34px, under the touch floor (ADR-0041).
            size: 'xl',
            'aria-label': 'Close navigation',
          }}
          padding={0}
          styles={{
            body: { height: '100%', padding: 0 },
            content: { background: 'var(--rc-bar)' },
            header: { background: 'var(--rc-bar)', minHeight: 54, paddingInline: 10 },
          }}
          zIndex={1000}
        >
          <WizardRail
            items={items}
            activeView={railActiveView}
            onNavigate={navigate}
            collapsed={false}
          />
        </Drawer>
      )}
    </Flex>
  );
}
