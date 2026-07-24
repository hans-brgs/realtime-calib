import { Box, Burger, Group, Image, Text } from '@mantine/core';

import { useCompactLayout } from '@/components/layout/useCompactLayout';

const NAV = ['Dashboard', 'Logs', 'Settings'] as const;

interface TopbarProps {
  burgerOpened: boolean;
  onBurger: () => void;
  // Opens the rig-level settings modal (ADR-0036) — the only live NAV tab so far.
  onSettings: () => void;
}

// Persistent top bar: wordmark (logo + realtime-calib with a violet hinge) on the
// left, a thin set of section tabs on the right. On phone the right tabs collapse
// to a burger that opens the navigation drawer.
export function Topbar({ burgerOpened, onBurger, onSettings }: TopbarProps) {
  // Same single source as the shell (ADR-0041): the burger appears exactly when the
  // rail stops being a column, so the two can never both show or both vanish.
  const compact = useCompactLayout();

  return (
    <Box
      h={54}
      px="md"
      style={{
        flex: '0 0 auto',
        background: 'var(--rc-bar)',
        borderBottom: '1px solid var(--mantine-color-dark-4)',
      }}
    >
      <Group h="100%" justify="space-between" wrap="nowrap">
        <Group gap={10} wrap="nowrap">
          <Image src="/logo.png" h={22} w="auto" alt="realtime-calib" style={{ opacity: 0.95 }} />
          <Text ff="heading" fw={600} fz="0.97rem" style={{ letterSpacing: '-0.01em' }}>
            realtime
            <Text span c="violet.4" inherit>
              -
            </Text>
            calib
          </Text>
        </Group>

        {!compact && (
          <Group gap={26} wrap="nowrap">
            {NAV.map((label, i) => (
              <Text
                key={label}
                fz="0.84rem"
                c={i === 0 ? undefined : 'dark.2'}
                onClick={label === 'Settings' ? onSettings : undefined}
                style={
                  i === 0
                    ? {
                        position: 'relative',
                        paddingBottom: 4,
                        borderBottom: '2px solid var(--rc-accent)',
                      }
                    : { cursor: 'pointer' }
                }
              >
                {label}
              </Text>
            ))}
          </Group>
        )}

        {compact && (
          <Burger opened={burgerOpened} onClick={onBurger} size="sm" aria-label="Menu" />
        )}
      </Group>
    </Box>
  );
}
