import { Box, Group, Text } from '@mantine/core';

import type { Covisibility } from '@/features/telemetry/telemetrySlice';

// Live co-visibility matrix (ADR-0007/0023): per camera pair, how many synchronized
// groups saw the board in BOTH cameras. Green when a pair has enough joint views to
// be stereo-initialised; the diagonal shows each camera's own detection tally.
const PAIR_TARGET = 5; // matches the solver's default min_shared

function cellColor(count: number): string {
  if (count >= PAIR_TARGET) return 'rgba(74, 222, 128, 0.35)';
  if (count > 0) return 'rgba(251, 191, 36, 0.25)';
  return 'var(--rc-input)';
}

function pairCount(data: Covisibility, a: string, b: string): number {
  return (
    data.pairs.find(
      (pair) => (pair.a === a && pair.b === b) || (pair.a === b && pair.b === a),
    )?.count ?? 0
  );
}

export function CovisibilityMatrix({ data }: { data: Covisibility | null }) {
  const cameras = data?.cameras ?? [];
  if (!data || cameras.length === 0) {
    return (
      <Text fz="0.72rem" c="dark.3">
        Co-visibility appears once the sweep starts.
      </Text>
    );
  }
  const short = (name: string) => name.replace(/^.*_/, '');
  return (
    <>
      <Box
        style={{
          display: 'grid',
          gridTemplateColumns: `auto repeat(${cameras.length}, 1fr)`,
          gap: 3,
        }}
      >
        <span />
        {cameras.map((name) => (
          <Text key={`col-${name}`} fz="0.62rem" c="dark.3" ta="center">
            {short(name)}
          </Text>
        ))}
        {cameras.map((row) => (
          <Box key={`row-${row}`} style={{ display: 'contents' }}>
            <Text fz="0.62rem" c="dark.3" pr={4} style={{ alignSelf: 'center' }}>
              {short(row)}
            </Text>
            {cameras.map((col) => {
              const diagonal = row === col;
              const count = diagonal
                ? (data.board_frames[row] ?? 0)
                : pairCount(data, row, col);
              return (
                <Box
                  key={`${row}-${col}`}
                  title={diagonal ? `${row} board frames` : `${row} × ${col}`}
                  style={{
                    borderRadius: 4,
                    minHeight: 26,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: diagonal ? 'transparent' : cellColor(count),
                    border: diagonal
                      ? '1px dashed var(--rc-border)'
                      : '1px solid rgba(255,255,255,0.04)',
                  }}
                >
                  <Text fz="0.68rem" fw={600} className="rc-tnum" c={diagonal ? 'dark.3' : undefined}>
                    {count}
                  </Text>
                </Box>
              );
            })}
          </Box>
        ))}
      </Box>
      <Group justify="space-between" mt="xs">
        <Text fz="0.62rem" c="dark.3">
          pair target ≥ {PAIR_TARGET}
        </Text>
        <Text fz="0.62rem" c="dark.3" className="rc-tnum">
          {data.synced_groups} synced groups
        </Text>
      </Group>
    </>
  );
}
