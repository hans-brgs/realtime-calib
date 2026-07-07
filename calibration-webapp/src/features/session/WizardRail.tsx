import { Box, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core';
import {
  IconCamera,
  IconCheck,
  IconChevronLeft,
  IconCircle,
  IconCircleFilled,
  IconDownload,
  IconFocusCentered,
  IconHome,
  IconLayoutGrid,
  IconLock,
  IconTopologyStar3,
  type IconProps,
} from '@tabler/icons-react';
import type { ComponentType } from 'react';

import type { StageStatus, ViewId } from '@/features/session/selectors';

type RailStatus = StageStatus | 'home';

export interface RailItem {
  id: ViewId;
  label: string;
  status: RailStatus;
}

const ICONS: Record<ViewId, ComponentType<IconProps>> = {
  session: IconHome,
  cameras: IconCamera,
  boards: IconLayoutGrid,
  intrinsic: IconFocusCentered,
  extrinsic: IconTopologyStar3,
  export: IconDownload,
};

// Per-status trailing glyph (current / done / to do / locked). Mirrors the design
// system's "Rail item — FSM status" component.
function StatusGlyph({ status }: { status: RailStatus }) {
  switch (status) {
    case 'complete':
      return <IconCheck size={13} color="var(--rc-success)" />;
    case 'active':
      return <IconCircleFilled size={9} color="var(--rc-accent)" />;
    case 'todo':
      return <IconCircle size={12} color="var(--mantine-color-dark-3)" />;
    case 'locked':
      return <IconLock size={12} color="var(--mantine-color-dark-3)" />;
    default:
      return null;
  }
}

interface RailButtonProps {
  item: RailItem;
  selected: boolean;
  collapsed: boolean;
  onNavigate: (id: ViewId) => void;
}

function RailButton({ item, selected, collapsed, onNavigate }: RailButtonProps) {
  const Icon = ICONS[item.id];
  const locked = item.status === 'locked';

  const button = (
    <UnstyledButton
      onClick={() => !locked && onNavigate(item.id)}
      disabled={locked}
      aria-current={selected ? 'page' : undefined}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 11,
        justifyContent: collapsed ? 'center' : 'flex-start',
        padding: '10px 11px',
        minHeight: 42,
        borderRadius: 'var(--mantine-radius-md)',
        borderLeft: `2px solid ${selected ? 'var(--rc-accent)' : 'transparent'}`,
        background: selected ? 'rgba(167,139,250,0.12)' : 'transparent',
        color: selected ? 'var(--mantine-color-dark-0)' : 'var(--mantine-color-dark-2)',
        cursor: locked ? 'not-allowed' : 'pointer',
        opacity: locked ? 0.55 : 1,
      }}
    >
      <Icon
        size={18}
        stroke={1.8}
        color={selected ? 'var(--rc-accent-bright)' : 'var(--mantine-color-dark-2)'}
        style={{ flex: 'none' }}
      />
      {!collapsed && (
        <>
          <Text fz="0.84rem" style={{ flex: 1, whiteSpace: 'nowrap' }} inherit>
            {item.label}
          </Text>
          <StatusGlyph status={item.status} />
        </>
      )}
    </UnstyledButton>
  );

  return collapsed ? (
    <Tooltip label={item.label} position="right" withArrow>
      {button}
    </Tooltip>
  ) : (
    button
  );
}

interface WizardRailProps {
  items: RailItem[];
  activeView: ViewId;
  onNavigate: (id: ViewId) => void;
  collapsed: boolean;
  onToggleCollapse?: () => void;
}

// Persistent FSM navigation rail (replaces the horizontal Stepper). Status is derived
// from session data (selectStages); the highlighted item is the currently-viewed
// screen. Collapses to a 70px icon strip on desktop; rendered expanded inside the
// mobile drawer (onToggleCollapse omitted there).
export function WizardRail({
  items,
  activeView,
  onNavigate,
  collapsed,
  onToggleCollapse,
}: WizardRailProps) {
  return (
    <Box
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--rc-bar)',
      }}
    >
      <Box
        px={14}
        pt={16}
        pb={14}
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}
      >
        {!collapsed && (
          <div style={{ minWidth: 0 }}>
            <Text ff="heading" fw={600} fz="0.84rem" style={{ whiteSpace: 'nowrap' }}>
              Calibration Wizard
            </Text>
            <Text fz="0.69rem" c="dark.3" style={{ whiteSpace: 'nowrap' }}>
              Precision Workflow
            </Text>
          </div>
        )}
        {onToggleCollapse && (
          <UnstyledButton
            onClick={onToggleCollapse}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            style={{
              flex: 'none',
              width: 30,
              height: 30,
              borderRadius: 'var(--mantine-radius-sm)',
              border: '1px solid var(--mantine-color-dark-4)',
              background: 'var(--rc-input)',
              color: 'var(--mantine-color-dark-2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <IconChevronLeft
              size={16}
              style={{ transform: collapsed ? 'rotate(180deg)' : 'none', transition: 'transform .18s' }}
            />
          </UnstyledButton>
        )}
      </Box>

      <Stack gap={3} px={10} py={6} style={{ flex: 1, overflowY: 'auto' }}>
        {items.map((item) => (
          <RailButton
            key={item.id}
            item={item}
            selected={item.id === activeView}
            collapsed={collapsed}
            onNavigate={onNavigate}
          />
        ))}
      </Stack>
    </Box>
  );
}
