import { Box, Group, Text } from '@mantine/core';
import { useEffect, useRef } from 'react';

// Results heatmap (ADR-0039): the quad-accumulation count of the RETAINED
// keyframes' detected-corner hulls — what actually constrained the solve.
// Intensity = redundancy (ramp). Note ChArUco corners are the board's INTERIOR
// lattice: a ~1-square margin along the image border can never light up at
// close board distances — that is physics, not a bug (edge positions: overflow
// the frame at a larger distance). Canvas-rendered: the map is ~96 cells wide
// (thousands of cells), painted one pixel per cell and upscaled with crisp
// edges (a DOM grid would melt on tablets).
interface CoverageHeatmapProps {
  grid: number[][];
}

const GREEN = [74, 222, 128] as const; // matches the gauges' success green

// Redundancy ramp: alpha at index min(count, last). Discrete shades so the
// operator reads levels, not a gradient. Tune freely after rig testing — adding
// entries adds shades (legend follows the ramp length automatically).
const RAMP = [0, 0.2, 0.38, 0.56, 0.74, 0.92] as const; // 0, 1x, 2x, 3x, 4x, 5+x

function alphaFor(count: number): number {
  return RAMP[Math.min(Math.max(count, 0), RAMP.length - 1)];
}

function LegendChip({ color, label }: { color: string; label: string }) {
  return (
    <Group gap={5} wrap="nowrap">
      <Box
        style={{
          width: 9,
          height: 9,
          borderRadius: 2,
          background: color,
          border: '1px solid rgba(255,255,255,0.08)',
        }}
      />
      <Text fz="0.62rem" c="dark.3">
        {label}
      </Text>
    </Group>
  );
}

export function CoverageHeatmap({ grid }: CoverageHeatmapProps) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const rows = grid.length;
  const cols = grid[0]?.length ?? 0;

  useEffect(() => {
    const element = canvas.current;
    if (!element || rows === 0 || cols === 0) return;
    const context = element.getContext('2d');
    if (!context) return;
    const image = context.createImageData(cols, rows);
    grid.forEach((row, r) =>
      row.forEach((count, c) => {
        const i = (r * cols + c) * 4;
        image.data[i] = GREEN[0];
        image.data[i + 1] = GREEN[1];
        image.data[i + 2] = GREEN[2];
        image.data[i + 3] = Math.round(alphaFor(count) * 255);
      }),
    );
    context.putImageData(image, 0, 0);
  }, [grid, rows, cols]);

  const top = RAMP.length - 1;
  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="sm" style={{ letterSpacing: '0.07em' }}>
        Field-of-view coverage
      </Text>
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 8,
          background: 'var(--rc-input)',
          borderRadius: 'var(--mantine-radius-md)',
        }}
      >
        <canvas
          ref={canvas}
          width={cols}
          height={rows}
          style={{
            // Contain preserves the map's aspect whatever the panel shape — a
            // width/max-height pair stretched it when the panel was wider than
            // the sensor ratio, distorting the perceived edge margins.
            width: '100%',
            height: '100%',
            objectFit: 'contain',
            imageRendering: 'pixelated',
            borderRadius: 4,
          }}
        />
      </Box>
      <Group justify="space-between" mt="xs" wrap="nowrap">
        <LegendChip color="rgba(255,255,255,0.05)" label="never" />
        <Group gap={3} wrap="nowrap">
          {RAMP.slice(1).map((alpha, i) => (
            <Box
              key={i}
              title={i + 1 >= top ? `${top}+× measured` : `${i + 1}× measured`}
              style={{
                width: 9,
                height: 9,
                borderRadius: 2,
                background: `rgba(${GREEN[0]},${GREEN[1]},${GREEN[2]},${alpha})`,
              }}
            />
          ))}
          <Text fz="0.62rem" c="dark.3" ml={2}>
            1× → {top}+× measured
          </Text>
        </Group>
      </Group>
    </Box>
  );
}
