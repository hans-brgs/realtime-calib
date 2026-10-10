import { Box, Group, Paper, ScrollArea, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import { STICKY_ACTION_GAP, STICKY_ACTION_LAYER } from '@/components/layout/StickyActionBar';
import { useSidePanel } from '@/components/layout/useSidePanel';
import {
  captureGridColumns,
  HERO_MEDIA_CEILING,
  HERO_VIEWPORT_BUDGET,
  useCompactLayout,
} from '@/components/layout/useCompactLayout';

interface CaptureWizardLayoutProps {
  // Optional control before the stepper (e.g. the intrinsic per-camera tabs): on the
  // stepper's row in the locked regime, above it in compact.
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
  // Compact-only: how the media area sizes itself.
  // - 'frame' (default): fixed-ratio media (live tile, replay scrubber, heatmap). The
  //   box takes the frame's 16:9 shape — portrait shows it exactly, landscape caps the
  //   height and pillarboxes.
  // - 'scene': an interactive 3D viewport. It has NO intrinsic ratio, so the frame
  //   treatment starves it in portrait (a narrow width × 9/16 ≈ a 200px strip): same
  //   ceiling as 'frame', but floored so portrait gives it real room.
  // - 'stack': a scrolling pile of tiles — floor only, each tile sizes itself.
  compactHero?: 'frame' | 'scene' | 'stack';
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
  compactHero = 'frame',
}: CaptureWizardLayoutProps) {
  const compact = useCompactLayout();
  const { collapsed: panelCollapsed } = useSidePanel();
  return (
    <>
      {/* One header row in the locked regime: the camera tabs, then the phases — two
          stacked rows took ~120px above the view. Compact stacks them, a phone has no
          width for both. */}
      {compact ? (
        <>
          {top && <Box mb="md">{top}</Box>}
          <Box mb="md">{stepper}</Box>
        </>
      ) : (
        // Wraps when the row is too narrow for both (many cameras, ~1000px): the
        // stepper then takes its own line instead of ellipsizing its labels. The tabs
        // scroll inside an overlay scrollbar, which takes no height.
        <Group wrap="wrap" gap="md" mb="md" align="center">
          {top && (
            <ScrollArea
              type="hover"
              scrollbarSize={4}
              style={{ flex: '0 1 auto', maxWidth: '100%' }}
            >
              {top}
            </ScrollArea>
          )}
          <Box style={{ flex: '1 1 440px', minWidth: 0 }}>{stepper}</Box>
        </Group>
      )}

      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: captureGridColumns(compact, panelCollapsed),
          // Folded: a second row under the view for the step's action (below).
          gridTemplateRows: panelCollapsed ? 'minmax(0, 1fr) auto' : undefined,
          gap: panelCollapsed ? 12 : 22,
        }}
      >
        <Box
          style={{
            minWidth: 0,
            // Locked: take whatever the row leaves. Flow: nothing above has a height to
            // inherit, so the box must set its own or the content collapses to zero.
            //
            // Media gets its own shape, capped by what the viewport can spend. Portrait:
            // the aspect ratio decides and the box is exactly as tall as the frame is
            // wide — no black bars, nothing pushed past the fold. Landscape: that same
            // box would be TALLER than the screen (a full-width 16:9 on a 402px-high
            // phone wants 474px), so the cap bites, the box stays full width, and the
            // frame pillarboxes inside it — the whole image visible, bands at the sides.
            ...(compact
              ? compactHero === 'stack'
                ? { minHeight: HERO_VIEWPORT_BUDGET }
                : // `width: 100%` is load-bearing. With the width left auto, the
                  // max-height TRANSFERS through the aspect ratio into a max-width
                  // (CSS "transferred size"), so the grid item shrinks to the frame's
                  // size and start-aligns — the "small view stuck on the left" bug.
                  // An explicit width disables the transfer: the box spans the column,
                  // the cap trims only its height, and the frame centers between
                  // black side bands (objectFit: contain does the rest).
                  //
                  // 'scene' adds a floor: the ratio alone would hand a 3D viewport a
                  // ~200px strip in portrait. The floor never bites in landscape
                  // (56vh of a short viewport sits under the ratio height there).
                  {
                    width: '100%',
                    aspectRatio: '16 / 9',
                    maxHeight: HERO_MEDIA_CEILING,
                    ...(compactHero === 'scene' ? { minHeight: HERO_VIEWPORT_BUDGET } : {}),
                  }
              : { minHeight: 0 }),
            position: 'relative',
            // Isolated stacking context: drei's <Html> labels in the 3D scenes carry
            // z-indexes in the MILLIONS (default zIndexRange ~16.7M), which would
            // otherwise punch through every app overlay — the full-page drawer
            // (z 1000), modals (z 200), popovers. Isolation caps their reach at this
            // box; overlays portal to <body> and stack above it.
            isolation: 'isolate',
          }}
        >
          {main}
        </Box>

        <Paper
          radius="lg"
          withBorder
          p={16}
          style={{
            minHeight: 0,
            // Locked: the panel is its own scroll container. Flow: the PAGE scrolls,
            // so scrolling here too would trap the settings inside a short box.
            overflowY: compact ? 'visible' : 'auto',
            display: panelCollapsed ? 'none' : 'flex',
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
            pt="md"
            style={
              compact
                ? {
                    // Owned here rather than by each panel's trailing element, which
                    // left the rule glued to the last line of text on most steps.
                    marginTop: STICKY_ACTION_GAP,
                    position: 'sticky',
                    bottom: 0,
                    // Full-bleed inside the panel: its 16px padding would otherwise
                    // leave side gutters for content to scroll through beside the bar.
                    marginInline: -16,
                    paddingInline: 16,
                    paddingBottom: 16,
                    background: 'var(--rc-panel)',
                    borderTop: '1px solid var(--rc-border)',
                    // Same layer as StickyActionBar: this panel scrolls its own
                    // SegmentedControls and Sliders under the bar, and at z-index 1
                    // their labels and thumbs painted through it.
                    zIndex: STICKY_ACTION_LAYER,
                  }
                : // Locked: no rule to space, and the action is pushed to the bottom
                  // of the panel it shares with the content above it.
                  { marginTop: 'auto' }
            }
          >
            {compact && message && (
              <Text fz="0.72rem" c="var(--rc-error)" mb="xs">
                {message}
              </Text>
            )}
            {!panelCollapsed && action}
          </Box>
        </Paper>

        {/* Folded panel: the step's action (Start/Stop REC, Compute, Validate…) and its
            error stay reachable under the view, at the panel's width — folding is for
            a wider view while recording, not for losing the Stop button (ADR-0041 §5:
            the action stays reachable). Rendered here instead of in the hidden panel,
            never in both. */}
        {panelCollapsed && (
          <Box style={{ justifySelf: 'end', width: 'clamp(280px, 26%, 360px)' }}>
            {message && (
              <Text fz="0.72rem" c="var(--rc-error)" mb="xs">
                {message}
              </Text>
            )}
            {action}
          </Box>
        )}
      </Box>
    </>
  );
}
