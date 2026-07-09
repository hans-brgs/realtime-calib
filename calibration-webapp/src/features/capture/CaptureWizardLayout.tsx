import { Box, Text } from '@mantine/core';
import type { ReactNode } from 'react';

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
  return (
    <>
      {top}
      {stepper}

      <Box
        className="rc-camsetup-grid"
        style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 22 }}
      >
        <Box style={{ minWidth: 0, minHeight: 0, position: 'relative' }}>{main}</Box>

        <Box
          style={{
            minHeight: 0,
            overflowY: 'auto',
            border: '1px solid var(--rc-border)',
            borderRadius: 'var(--mantine-radius-lg)',
            background: 'var(--rc-panel)',
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {panel}
          {message && (
            <Text fz="0.72rem" c="var(--rc-error)" mt="sm">
              {message}
            </Text>
          )}
          <Box mt="auto" pt="md">
            {action}
          </Box>
        </Box>
      </Box>
    </>
  );
}
