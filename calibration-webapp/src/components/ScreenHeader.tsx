import { Box, Group, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import { InfoPopover } from '@/components/InfoPopover';
import { SidePanelToggle } from '@/components/layout/SidePanel';
import { useCompactLayout } from '@/components/layout/useCompactLayout';

interface ScreenHeaderProps {
  title: string;
  // The screen's explanation, behind an info button next to the title rather than an
  // always-on subtitle: the views below need the height (rig test 2026-10-10).
  info?: ReactNode;
  right?: ReactNode;
  // The screen has a foldable right panel: its toggle closes the header row, above
  // the panel it folds.
  panelToggle?: boolean;
}

// Per-screen header: Sora screen-title (h2 / 21px) + its info button, with optional
// right-aligned actions. Shared across every wizard screen.
export function ScreenHeader({ title, info, right, panelToggle = false }: ScreenHeaderProps) {
  const compact = useCompactLayout();
  return (
    // Locked regime: no wrap, the title box gives way and the actions stay on the
    // title row. Compact: they wrap under it, a phone has no width to share.
    <Group
      justify="space-between"
      align="flex-start"
      wrap={compact ? 'wrap' : 'nowrap'}
      gap="md"
      mb="lg"
    >
      {/* Locked: basis 0 so the title box, not the actions, gives way. Compact: the
          natural width, so the actions can wrap under it. */}
      <Box style={compact ? undefined : { flex: 1, minWidth: 0 }}>
        {info ? (
          <Group gap={6} wrap="nowrap">
            <Title order={2}>{title}</Title>
            <InfoPopover label={`About ${title}`} width={300} position="bottom-start">
              {info}
            </InfoPopover>
          </Group>
        ) : (
          <Title order={2}>{title}</Title>
        )}
      </Box>
      {panelToggle && !compact ? (
        <Group gap={9} wrap="nowrap">
          {right}
          <SidePanelToggle />
        </Group>
      ) : (
        right
      )}
    </Group>
  );
}
