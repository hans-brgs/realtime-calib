import { Box, Group, Text, Tooltip } from '@mantine/core';

import { useCompactLayout } from '@/components/layout/useCompactLayout';

// Sub-step rail of the 4-phase flows (design "SUB-STEPPER", realtime-calib.dc.html):
// ONE joined bar, each phase a cell with an accent left bar, a round numbered badge
// (check when done) and its title; the hint is a tooltip, so the bar fits on one row
// beside the camera tabs (rig test 2026-10-10: the two stacked rows took ~120px above
// the view). Shared by the intrinsic and extrinsic screens. Display-only: transitions
// are driven by the screens' action buttons, not by clicking the rail. No outer
// margin: CaptureWizardLayout places it.
export interface Phase {
  key: string;
  label: string;
  sub: string;
}

// Flow regime: the four full cells never fit a phone's width — the labels clip
// (ADR-0041). Collapse to a single row (active phase + "n / m") over a segmented
// progress track: the same information (where am I, how many) in a strip that fits.
function CompactPhaseStepper({ phases, index }: { phases: Phase[]; index: number }) {
  const active = phases[Math.max(0, index)] ?? phases[0];
  return (
    <Box
      style={{
        border: '1px solid var(--rc-border)',
        borderRadius: 12,
        background: '#0e0e12',
        padding: '10px 13px',
      }}
    >
      <Group justify="space-between" wrap="nowrap" mb={9}>
        <Group gap={9} wrap="nowrap" style={{ minWidth: 0 }}>
          <Box
            component="span"
            className="rc-tnum"
            style={{
              width: 22,
              height: 22,
              flex: 'none',
              borderRadius: '50%',
              border: '1.5px solid var(--rc-accent)',
              background: 'rgba(167,139,250,0.18)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '0.7rem',
              fontWeight: 600,
              color: 'var(--rc-accent-bright)',
            }}
          >
            {index + 1}
          </Box>
          <Text
            fz="0.86rem"
            fw={600}
            ff="heading"
            style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {active?.label}
          </Text>
        </Group>
        <Text fz="0.72rem" c="dark.3" className="rc-tnum" style={{ flex: 'none' }}>
          {index + 1} / {phases.length}
        </Text>
      </Group>
      <Box style={{ display: 'flex', gap: 4 }}>
        {phases.map((phase, i) => (
          <Box
            key={phase.key}
            style={{
              flex: 1,
              height: 3,
              borderRadius: 2,
              background: i <= index ? 'var(--rc-accent)' : 'var(--mantine-color-dark-4)',
            }}
          />
        ))}
      </Box>
    </Box>
  );
}

export function PhaseStepper({ phases, current }: { phases: Phase[]; current: string }) {
  const index = phases.findIndex((phase) => phase.key === current);
  const compact = useCompactLayout();
  if (compact) {
    return <CompactPhaseStepper phases={phases} index={index} />;
  }
  return (
    <Box
      style={{
        display: 'flex',
        alignItems: 'stretch',
        border: '1px solid var(--rc-border)',
        borderRadius: 12,
        overflow: 'hidden',
        background: '#0e0e12',
      }}
    >
      {phases.map((phase, i) => {
        const active = i === index;
        const done = i < index;
        return (
          <Tooltip key={phase.key} label={phase.sub} openDelay={300} withArrow>
            <Box
              style={{
                flex: 1,
                minWidth: 0,
                display: 'flex',
                alignItems: 'center',
                gap: 9,
                minHeight: 38,
                padding: '0 12px',
                background: active ? 'rgba(167,139,250,0.1)' : 'transparent',
                borderLeft: `2px solid ${active ? 'var(--rc-accent)' : 'transparent'}`,
              }}
            >
              <Box
                component="span"
                className="rc-tnum"
                style={{
                  width: 22,
                  height: 22,
                  flex: 'none',
                  borderRadius: '50%',
                  border: `1.5px solid ${
                    active ? 'var(--rc-accent)' : done ? 'var(--rc-success)' : '#3a3a44'
                  }`,
                  background: active
                    ? 'rgba(167,139,250,0.18)'
                    : done
                      ? 'rgba(52,211,153,0.14)'
                      : 'transparent',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '0.68rem',
                  fontWeight: 600,
                  color: active
                    ? 'var(--rc-accent-bright)'
                    : done
                      ? 'var(--rc-success)'
                      : 'var(--rc-text-dim)',
                }}
              >
                {done ? '✓' : i + 1}
              </Box>
              <Text
                fz="0.8rem"
                fw={600}
                ff="heading"
                c={active ? undefined : done ? 'dark.1' : 'dark.2'}
                style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
              >
                {phase.label}
              </Text>
            </Box>
          </Tooltip>
        );
      })}
    </Box>
  );
}
