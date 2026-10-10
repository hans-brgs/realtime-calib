import { Box, Group, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

import { InfoPopover } from '@/components/InfoPopover';
import { SidePanelToggle } from '@/components/layout/SidePanel';
import { useCompactLayout } from '@/components/layout/useCompactLayout';

interface ScreenHeaderProps {
  title: string;
  subtitle?: ReactNode;
  // The screen's explanation behind an info button next to the title, instead of an
  // always-on subtitle: for the capture steps, whose view needs the height.
  info?: ReactNode;
  right?: ReactNode;
  // The screen has a foldable right panel: its toggle closes the header row, above
  // the panel it folds.
  panelToggle?: boolean;
}

// Per-screen header: Sora screen-title (h2 / 21px) + muted subtitle, with optional
// right-aligned actions. Shared across every wizard screen.
export function ScreenHeader({
  title,
  subtitle,
  info,
  right,
  panelToggle = false,
}: ScreenHeaderProps) {
  const compact = useCompactLayout();
  return (
    // Locked regime: no wrap, the subtitle gives way and the actions stay on the title
    // row (with the panel toggle they no longer fit beside a full subtitle at ~1200px).
    // Compact: they wrap under it, a phone has no width to share.
    <Group
      justify="space-between"
      align="flex-start"
      wrap={compact ? 'wrap' : 'nowrap'}
      gap="md"
      mb="lg"
    >
      {/* Locked: basis 0 so the subtitle, not the actions, gives way. Compact: the
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
        {subtitle ? (
          <Text c="dark.2" fz="0.84rem" mt={6} maw={640}>
            {subtitle}
          </Text>
        ) : null}
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
