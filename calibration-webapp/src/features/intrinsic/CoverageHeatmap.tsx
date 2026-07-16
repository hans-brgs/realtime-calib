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
// entries adds shades (legend + top label follow the ramp length automatically).
// Currently 1x..10+x (rig trial): finer redundancy read-out, at the cost of
// smaller steps between adjacent shades. Revert by shortening this array.
const RAMP = [0, 0.12, 0.21, 0.3, 0.39, 0.48, 0.56, 0.65, 0.74, 0.83, 0.92] as const;

// The legend row's own height, reserved so the canvas + legend column as a whole
// fits the panel (they share one width, so the canvas cannot claim it all).
const LEGEND_ROW_PX = 24;

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
          // Deepest surface: everything OUTSIDE the sensor recedes, so the frame
          // below reads as a raised rectangle rather than blending into it.
          background: 'var(--rc-page)',
          borderRadius: 'var(--mantine-radius-md)',
          // Container-query units size the sensor column to the largest
          // sensor-ratio rectangle that fits, whatever the panel shape.
          containerType: 'size',
        }}
      >
        {/* Sensor column: carries THE width, so the canvas and the legend share
            it by construction — the legend used to span the panel instead, and
            drifted away from the frame whenever the panel was the wider one. */}
        <Box
          style={{
            width:
              cols > 0 && rows > 0
                ? `min(100cqw, calc((100cqh - ${LEGEND_ROW_PX}px) * ${cols / rows}))`
                : '100%',
          }}
        >
          <canvas
            ref={canvas}
            width={cols}
            height={rows}
            style={{
              // The ELEMENT is the sensor frame (ADR-0039 display fix): object-fit
              // letterboxed the 16:9 map invisibly inside the panel-shaped element
              // (transparent "never" cells = same background as the letterbox), so
              // the map READ as the panel's ratio and the edge margins looked
              // inflated. Height follows the bitmap's intrinsic cols:rows ratio.
              display: 'block',
              width: '100%',
              imageRendering: 'pixelated',
              // Three distinct luminance levels — outside (page) < sensor (input)
              // < frame (dim). The old border token (dark-4 = #232329) sat within
              // six points of both surfaces: technically drawn, visually absent.
              border: '1px solid var(--mantine-color-dark-3)',
              background: 'var(--rc-input)',
              borderRadius: 4,
            }}
          />
          <Group justify="space-between" mt={7} wrap="nowrap" gap="xs">
            {/* Matches what an uncovered cell actually shows: the canvas background
                through a transparent cell (alpha 0 in the ramp). */}
            <LegendChip color="var(--rc-input)" label="never" />
            <Group gap={2} wrap="nowrap">
              {RAMP.slice(1).map((alpha, i) => (
                <Box
                  key={i}
                  title={i + 1 >= top ? `${top}+× measured` : `${i + 1}× measured`}
                  style={{
                    width: 9,
                    height: 9,
                    borderRadius: 2,
                    flex: 'none',
                    background: `rgba(${GREEN[0]},${GREEN[1]},${GREEN[2]},${alpha})`,
                  }}
                />
              ))}
              <Text fz="0.62rem" c="dark.3" ml={4} style={{ whiteSpace: 'nowrap' }}>
                1× → {top}+× measured
              </Text>
            </Group>
          </Group>
        </Box>
      </Box>
    </Box>
  );
}
