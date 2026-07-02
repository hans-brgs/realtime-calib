import { Box, Group, Text } from '@mantine/core';

// Results heatmap (ADR-0022): the normalised sensor-occupancy grid (rows x cols in
// [0,1]) from the corners used in the solve — shows where the board did / did not cover
// the field of view. Empty cells (gaps) stay dark; well-covered cells glow green.
interface CoverageHeatmapProps {
  grid: number[][];
}

export function CoverageHeatmap({ grid }: CoverageHeatmapProps) {
  const rows = grid.length;
  const cols = grid[0]?.length ?? 0;
  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="sm" style={{ letterSpacing: '0.07em' }}>
        Field-of-view coverage
      </Text>
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gridTemplateRows: `repeat(${rows}, 1fr)`,
          gap: 3,
          padding: 8,
          background: 'var(--rc-input)',
          borderRadius: 'var(--mantine-radius-md)',
        }}
      >
        {grid.flatMap((row, r) =>
          row.map((value, c) => (
            <Box
              key={`${r}-${c}`}
              title={`${Math.round(value * 100)}%`}
              style={{
                borderRadius: 2,
                background: `rgba(74, 222, 128, ${value.toFixed(3)})`,
                border: '1px solid rgba(255,255,255,0.03)',
              }}
            />
          )),
        )}
      </Box>
      <Group justify="space-between" mt="xs">
        <Text fz="0.62rem" c="dark.3">
          gap
        </Text>
        <Text fz="0.62rem" c="dark.3">
          well covered
        </Text>
      </Group>
    </Box>
  );
}
