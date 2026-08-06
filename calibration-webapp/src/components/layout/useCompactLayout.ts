import { useMantineTheme } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';

// Single source of truth for the responsive layout regime (ADR-0041).
//
// `compact` is true when the viewport is portrait OR narrower than the `md`
// breakpoint. It drives the WHOLE layout contract: whether the shell locks to one
// viewport height or lets the page scroll, whether the capture screens show one or
// two columns, where the primary action sits, and the preview grid geometry. One
// query, one boolean — the three divergent thresholds this replaces (rail at `sm`,
// grid at 62em in CSS, preview reflow at `sm`-or-portrait, duplicated) are what
// made phone and tablet-portrait unusable.
//
// The breakpoint is READ FROM THE THEME, never re-typed here: duplicating the value
// would recreate exactly the drift this ADR removes. The `calc(md - 0.0625em)` form
// is what Mantine itself generates for `hiddenFrom`/`visibleFrom`, so the rail
// switch and the layout switch can never disagree at the boundary pixel.
export function useCompactLayout(): boolean {
  const theme = useMantineTheme();
  // Read synchronously on first render: this is a client-only SPA (Vite +
  // createRoot, no SSR), so deferring to an effect would only buy a one-frame
  // flash of the desktop layout on a phone.
  return (
    useMediaQuery(
      `(max-width: calc(${theme.breakpoints.md} - 0.0625em)), (orientation: portrait)`,
      false,
      { getInitialValueInEffect: false },
    ) ?? false
  );
}

// Root height for a wizard screen, shared by the five capture screens (ADR-0041).
// Locked regime: fill the shell exactly. Flow regime: no fixed height at all, so the
// content sets it and the page scrolls past it. A hard `100%` in flow is precisely
// what pinned each screen to the viewport and left everything below the fold both
// squashed and unreachable on phones.
export function screenHeight(compact: boolean): '100%' | undefined {
  return compact ? undefined : '100%';
}

// Columns of the capture layout: big view on the left, settings panel on the right.
//
// Locked: the panel is FLUID, not the fixed 300-380px block it used to be. That block
// was the bug — only the centre column (`1fr`) gave space back when the rail expanded,
// so the information-bearing view shrank while the settings kept their full width.
// With a clamp, both columns share the give-back. Bounds: never below 280px (the
// widest knob rows stop wrapping there), never above 360px (past that it just steals
// from the view).
//
// Flow: a single column. The screens render the view first and the panel after it, so
// stacking puts the settings under the view, which is what an operator on a phone
// expects to scroll to.
export function captureGridColumns(compact: boolean): string {
  return compact ? 'minmax(0, 1fr)' : 'minmax(0, 1fr) clamp(280px, 26%, 360px)';
}

// FLOOR for hero content with no intrinsic size (a code preview, a stack of camera
// tiles) in the flow regime, where no ancestor has a height to inherit — without it
// the box collapses to nothing. 56vh keeps the panel below it within reach.
export const HERO_VIEWPORT_BUDGET = 'min(56vh, 520px)';

// CEILING for the single-media hero box (live tile, scrubber, heatmap, 3D scene).
// It only ever bites in landscape, where a full-width 16:9 box is taller than the
// viewport: the box keeps its width, the frame pillarboxes inside it, and at 90dvh
// the image spends nearly the whole screen height (the panel scrolls in below).
// Portrait is untouched — there the aspect height is always well under this.
export const HERO_MEDIA_CEILING = '90dvh';
