import { Box } from '@mantine/core';
import type { ReactNode } from 'react';

import { useCompactLayout } from '@/components/layout/useCompactLayout';

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
        marginTop: 'auto',
        // Own the vertical rhythm in compact: children are lifted out of the parent's
        // flex `gap` into this Box, so re-establish a comparable gap here.
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        paddingTop: 12,
        paddingBottom: 12,
        background: bg,
        borderTop: '1px solid var(--rc-border)',
        zIndex: 1,
      }}
    >
      {children}
    </Box>
  );
}
