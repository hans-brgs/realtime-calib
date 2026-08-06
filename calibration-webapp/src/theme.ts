import { Button, Card, createTheme, Paper, Progress, SegmentedControl } from '@mantine/core';
import type { CSSProperties } from 'react';

// realtime-calib design system (imported from Claude Design "Design System.dc.html").
// Dark, violet-accented language: Sora for display, Manrope for body + tabular values,
// layered near-black cool surfaces. Semantic colors live in `other` (and as CSS vars
// in index.css for non-React usage). See ADR design-system.

// Self-hosted variable fonts (@fontsource-variable/*), imported in main.tsx. The
// "Variable" family name is what those packages register.
const SORA = "'Sora Variable', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
const MANROPE =
  "'Manrope Variable', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

// Violet accent ramp (filled default = shade 6 = #7c3aed; accent text = shade 4 #a78bfa).
const violet: [string, string, string, string, string, string, string, string, string, string] = [
  '#f5f3ff',
  '#ede9fe',
  '#ddd6fe',
  '#c4b5fd',
  '#a78bfa',
  '#8b5cf6',
  '#7c3aed',
  '#6d28d9',
  '#5b21b6',
  '#4c1d95',
];

// Cool near-black surface ramp. Mantine dark scheme maps: text = dark[0], dimmed = dark[2],
// border = dark[4], surface (default) = dark[6], surface-hover = dark[5], body = dark[7].
const dark: [string, string, string, string, string, string, string, string, string, string] = [
  '#ececf0', // 0 text primary
  '#cfcfd6', // 1 text strong
  '#9a9aa5', // 2 text muted / dimmed
  '#6a6a74', // 3 text dim
  '#232329', // 4 border
  '#16161b', // 5 input / raised
  '#0f0f12', // 6 panel / card
  '#0c0c0f', // 7 sidebar / topbar / body
  '#0a0a0e', // 8 page (deepest; hue-shifted off the bar so controls never blend in)
  '#050506', // 9
];

export const theme = createTheme({
  primaryColor: 'violet',
  primaryShade: { light: 6, dark: 6 },
  autoContrast: true,
  colors: { violet, dark },

  fontFamily: MANROPE,
  fontFamilyMonospace: MANROPE,
  headings: {
    fontFamily: SORA,
    fontWeight: '600',
    sizes: {
      h1: { fontSize: '1.6875rem', lineHeight: '1.2' }, // 27px page H1
      h2: { fontSize: '1.3125rem', lineHeight: '1.25' }, // 21px screen H1
      h3: { fontSize: '1rem', lineHeight: '1.3' }, // 16px section
    },
  },

  defaultRadius: 'md',
  radius: {
    xs: '0.4375rem', // 7
    sm: '0.5rem', // 8
    md: '0.625rem', // 10
    lg: '0.75rem', // 12
    xl: '0.875rem', // 14
  },
  spacing: {
    xs: '0.5rem', // 8
    sm: '0.75rem', // 12
    md: '1rem', // 16
    lg: '1.375rem', // 22
    xl: '1.625rem', // 26
  },

  // Semantic + accent tokens (board fill ramp red→amber→green, status, accent variants).
  other: {
    accent: '#a78bfa',
    accentBright: '#c4b5fd',
    accentDeep: '#8b5cf6',
    success: '#34d399',
    warning: '#fbbf24',
    error: '#f87171',
    surfacePage: '#0a0a0e',
    surfaceBar: '#0c0c0f',
    surfacePanel: '#0f0f12',
    surfaceInput: '#16161b',
    // Text emphasis ramp (descending) — full design ramp beyond the Mantine dark scale.
    text: '#ececf0',
    textStrong: '#cfcfd6',
    textMuted: '#9a9aa5',
    textSubtle: '#7a7a85',
    textDim: '#6a6a74',
    textFaint: '#4a4a54',
    // Quality gauge fill (red → amber → green), reused by live gauges (Phase 3).
    gauge: 'linear-gradient(90deg, #f87171, #fbbf24, #34d399)',
  },

  components: {
    Paper: Paper.extend({
      defaultProps: { bg: 'dark.6' },
    }),
    Card: Card.extend({
      defaultProps: { radius: 'lg', withBorder: true, bg: 'dark.6' },
    }),
    Button: Button.extend({
      defaultProps: { radius: 'md' },
      styles: { label: { fontWeight: 600 } },
    }),
    Progress: Progress.extend({
      defaultProps: { radius: 'xl', size: 'sm' },
    }),
    // Raised control surface + accent thumb (design seg()): Mantine's dark default
    // put the root on dark[8] = the page color, making the control invisible.
    SegmentedControl: SegmentedControl.extend({
      styles: {
        root: {
          background: 'var(--rc-input)',
          border: '1px solid var(--rc-border)',
        },
        indicator: { background: 'rgba(167, 139, 250, 0.16)' },
      },
    }),
  },
});

// Secondary action that DESTROYS work (the "Re-record" buttons: a new take overwrites
// the recording and discards its calibration result). Red carries the same warning as
// the confirm modal each of them opens, and separates them from the safe violet action
// they sit under.
//
// Not Mantine's `color="red" variant="light"` as-is: that fill is a solid rgb(101,21,21)
// block, far heavier than everything around it. This is a thin wash in the alpha family
// used for tinted surfaces elsewhere (rail selection 0.12, segmented indicator 0.16).
// Applied through the component's CSS variables, NOT raw `background`/`border`: an inline
// background would outrank Mantine's hover rule and kill the hover state.
export const DESTRUCTIVE_BUTTON_VARS = {
  '--button-bg': 'rgba(248, 113, 113, 0.10)',
  '--button-hover': 'rgba(248, 113, 113, 0.18)',
  '--button-color': 'var(--rc-error)',
  '--button-bd': '1px solid rgba(248, 113, 113, 0.45)',
} as CSSProperties;
