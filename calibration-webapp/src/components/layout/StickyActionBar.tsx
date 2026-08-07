import { Box } from '@mantine/core';
import type { ReactNode } from 'react';

import { useCompactLayout } from '@/components/layout/useCompactLayout';

// Paint layer for every sticky action bar in the flow regime — shared so the two
// implementations (this one and CaptureWizardLayout's in-panel bar) cannot drift.
//
// It has to clear the panel content scrolling underneath it, and that content sets
// z-indexes the bar does not control. Mantine puts them on positioned descendants of
// roots that are `position: relative; z-index: auto` — which create NO stacking
// context — so they leak into whichever context the bar competes in: SegmentedControl's
// labels and controls sit at 2, Table's sticky cells reach 5, Skeleton's shimmer 11.
// At the old value of 1 the ChArUco/ArUco labels painted straight through the Save
// button on Target Config. 20 clears the lot with headroom.
//
// The ceiling matters as much as the floor: overlays portal to <body> and MUST keep
// painting above the bar — Mantine's Modal defaults to 200, notifications to 400, and
// the wizard's full-page drawer is pinned at 1000. 20 stays far below all of them.
export const STICKY_ACTION_LAYER = 20;

// Breathing room between the panel content and the separator, shared by both bars.
//
// It has to be owned HERE, not by each panel's last element. Every panel puts its
// spacing on top (`mt="md"`) and nothing at the bottom, so the gap above the rule was
// whatever the trailing element happened to leave: zero on the Result, Prepare and
// Co-visibility panels, where the last line of text ended up glued to the rule. The
// one panel that looked right — the live gauges — only did so by accident: its last
// block reserves two lines for a message that wraps at narrow widths, so a blank line
// showed through. Spacing that depends on what a panel happens to end with drifts per
// step, which is exactly what this fixes.
//
// 16 matches the padding below the rule, so the separator sits in symmetric space.
export const STICKY_ACTION_GAP = 16;

// Pins a screen's primary action to the bottom of the viewport in the flow regime
// (ADR-0041). There the settings panel stacks under the big view and the page scrolls,
// so an action at the end of the panel can sit far below the fold — Start / Compute /
// Save / Apply must stay one tap away. In the locked regime this is a no-op: it renders
// its children inline, exactly where they were, since the panel is already fully in view.
//
// Contract: for the bar to stick to the VIEWPORT (not to an inner box), every ancestor
// up to the document must have visible overflow while compact. The caller's scroll panel
// is `overflowY: 'auto'` in the locked regime, so it must switch to 'visible' when
// compact — otherwise the bar sticks to that panel, which never scrolls, and nothing
// moves. `bg` must match the panel it sits in so scrolled content is occluded behind it.
export function StickyActionBar({
  children,
  bg = 'var(--rc-page)',
}: {
  children: ReactNode;
  bg?: string;
}) {
  const compact = useCompactLayout();
  // Desktop: a transparent pass-through, so children keep whatever spacing their
  // parent already gives them (e.g. a flex `gap`). Wrapping them in a Box here would
  // sever that and silently collapse the layout.
  if (!compact) return <>{children}</>;
  return (
    <Box
      style={{
        position: 'sticky',
        bottom: 0,
        marginTop: STICKY_ACTION_GAP,
        // Own the vertical rhythm in compact: children are lifted out of the parent's
        // flex `gap` into this Box, so re-establish a comparable gap here.
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        paddingTop: 12,
        paddingBottom: 12,
        background: bg,
        borderTop: '1px solid var(--rc-border)',
        zIndex: STICKY_ACTION_LAYER,
      }}
    >
      {children}
    </Box>
  );
}
