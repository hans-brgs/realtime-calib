import { ActionIcon, Tooltip } from '@mantine/core';
import { IconLayoutSidebarRightCollapse, IconLayoutSidebarRightExpand } from '@tabler/icons-react';
import { type ReactNode, useState } from 'react';

import { useCompactLayout } from '@/components/layout/useCompactLayout';
import { SidePanelContext, useSidePanel } from '@/components/layout/useSidePanel';

// The right-hand settings panel of the wizard screens can be folded away to give the
// view (camera tiles, replay, 3D scene) the whole width — the counterpart of the
// left rail's collapse. One state for every step, held by the shell like the rail's,
// so the choice follows the operator from screen to screen; volatile UI state, never
// persisted (ADR-0011).
//
// Locked regime only (ADR-0041): see useSidePanel.

export function SidePanelProvider({ children }: { children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <SidePanelContext value={{ collapsed, toggle: () => setCollapsed((c) => !c) }}>
      {children}
    </SidePanelContext>
  );
}

// The fold/unfold button, placed in the screen header above the panel. Renders
// nothing in `compact`.
export function SidePanelToggle() {
  const compact = useCompactLayout();
  const { collapsed, toggle } = useSidePanel();
  if (compact) {
    return null;
  }
  const label = collapsed ? 'Show the side panel' : 'Hide the side panel';
  const Icon = collapsed ? IconLayoutSidebarRightExpand : IconLayoutSidebarRightCollapse;
  return (
    <Tooltip label={label} position="left" withArrow>
      <ActionIcon variant="default" size={38} radius="md" aria-label={label} onClick={toggle}>
        <Icon size={18} />
      </ActionIcon>
    </Tooltip>
  );
}
