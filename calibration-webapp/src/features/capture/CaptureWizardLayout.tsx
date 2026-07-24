import { Box, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import {
  captureGridColumns,
  HERO_MIN_HEIGHT,
  useCompactLayout,
} from '@/components/layout/useCompactLayout';

interface CaptureWizardLayoutProps {
  // Optional row above the stepper (e.g. the intrinsic per-camera SegmentedControl).
  top?: ReactNode;
  stepper: ReactNode;
  // Left area (the big preview / scrubber / 3D scene) — the screen picks it by step.
  main: ReactNode;
  // Right dashboard content — the screen picks it by step.
  panel: ReactNode;
  // Action buttons, pinned to the bottom of the right panel.
  action: ReactNode;
  // Surfaced error, shown just above the action block.
  message?: string | null;
}

// Presentational shell for the capture sub-wizard (D5): the two-column grid
// (preview | 300px dashboard), the bordered scrollable right panel, and the
// bottom-pinned action block. Pure chrome — it knows nothing about steps; the screen
// switches main / panel / action by wizard.step and keeps its own modals + scrubbers.
export function CaptureWizardLayout({
  top,
  stepper,
  main,
  panel,
  action,
  message,
}: CaptureWizardLayoutProps) {
  const compact = useCompactLayout();
  return (
    <>
      {top}
      {stepper}

      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: captureGridColumns(compact),
          gap: 22,
        }}
      >
        <Box
          style={{
            minWidth: 0,
            // Locked: take whatever the row leaves. Flow: nothing above has a height
            // to inherit, so without a floor the live view / scrubber / 3D scene
            // collapse to zero and the operator scrolls past an empty box.
            minHeight: compact ? HERO_MIN_HEIGHT : 0,
            position: 'relative',
          }}
        >
          {main}
        </Box>

        <Box
          style={{
            minHeight: 0,
            // Locked: the panel is its own scroll container. Flow: the PAGE scrolls,
            // so scrolling here too would trap the settings inside a short box.
            overflowY: compact ? 'visible' : 'auto',
            border: '1px solid var(--rc-border)',
            borderRadius: 'var(--mantine-radius-lg)',
            background: 'var(--rc-panel)',
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {panel}
          {/* Locked: the message sits after the panel content, the action is pushed to
              the bottom by `mt: auto`. Flow: the two travel together inside the sticky
              bar — an error scrolled away from the button it explains is worse than
              no error at all. */}
          {!compact && message && (
            <Text fz="0.72rem" c="var(--rc-error)" mt="sm">
              {message}
            </Text>
          )}
          <Box
            mt="auto"
            pt="md"
            style={
              compact
                ? {
                    position: 'sticky',
                    bottom: 0,
                    // Full-bleed inside the panel: its 16px padding would otherwise
                    // leave side gutters for content to scroll through beside the bar.
                    marginInline: -16,
                    paddingInline: 16,
                    paddingBottom: 16,
                    background: 'var(--rc-panel)',
                    borderTop: '1px solid var(--rc-border)',
                    zIndex: 1,
                  }
                : undefined
            }
          >
            {compact && message && (
              <Text fz="0.72rem" c="var(--rc-error)" mb="xs">
                {message}
              </Text>
            )}
            {action}
          </Box>
        </Box>
      </Box>
    </>
  );
}
