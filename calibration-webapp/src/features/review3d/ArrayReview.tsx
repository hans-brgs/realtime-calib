import { Bounds, Html, Line, TrackballControls } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { ActionIcon, Box, Button, Group, Slider, Text } from '@mantine/core';
import {
  IconAdjustments,
  IconCrosshair,
  IconPlayerPauseFilled,
  IconPlayerPlayFilled,
  IconWand,
  IconX,
} from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { useCompactLayout } from '@/components/layout/useCompactLayout';
import {
  type ExtrinsicResultPayload,
  minimizeExtrinsic,
  orientExtrinsic,
} from '@/transport/httpClient';

// Extrinsic Result 3D review (spec 3d-extrinsic-review): labeled camera frustums at
// their solved poses + the triangulated corner cloud of the scrubbed group + the
// board outline with its local xyz triad. The scene is shown in a fixed physical
// frame (Y-up right-handed, ADR-0026); the solved data stays canonical OpenCV. The
// export convention is an output codec chosen later at the Export step, not here.
type Vec3 = [number, number, number];

const CAMERA_COLOR = '#a78bfa'; // one hue for the whole rig: cameras read by label
const PLAY_FPS = 6;

// Fixed physical viewing frame (ADR-0026): the solved data is canonical OpenCV
// (Y-down); we display it Y-up right-handed so "up is up". There is NO convention
// selector here — the convention is an export codec, chosen at the Export step.
const VIEW_BASIS: number[][] = [
  [1, 0, 0],
  [0, -1, 0],
  [0, 0, -1],
];
const VIEW_UP: Vec3 = [0, 1, 0];

function rodriguesToMatrix(r: number[]): number[][] {
  const theta = Math.hypot(r[0], r[1], r[2]);
  if (theta < 1e-12) {
    return [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ];
  }
  const [kx, ky, kz] = [r[0] / theta, r[1] / theta, r[2] / theta];
  const c = Math.cos(theta);
  const s = Math.sin(theta);
  const v = 1 - c;
  return [
    [kx * kx * v + c, kx * ky * v - kz * s, kx * kz * v + ky * s],
    [kx * ky * v + kz * s, ky * ky * v + c, ky * kz * v - kx * s],
    [kx * kz * v - ky * s, ky * kz * v + kx * s, kz * kz * v + c],
  ];
}

const mulMV = (m: number[][], p: Vec3): Vec3 => [
  m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2],
  m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2],
  m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2],
];

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const scale = (a: Vec3, s: number): Vec3 => [a[0] * s, a[1] * s, a[2] * s];
const norm = (a: Vec3): number => Math.hypot(a[0], a[1], a[2]);
const unit = (a: Vec3): Vec3 => scale(a, 1 / (norm(a) || 1));
const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];

// A camera's world-space pose from the solved world->cam (R, t): position -R^T t,
// local axes = rows of R (cam axes expressed in world) -> columns of R^T.
interface CameraPose {
  name: string;
  position: Vec3;
  axisX: Vec3;
  axisY: Vec3;
  axisZ: Vec3;
}

function cameraPoses(result: ExtrinsicResultPayload): CameraPose[] {
  return result.cameras.map((name) => {
    const rot = rodriguesToMatrix(result.rotations[name] ?? [0, 0, 0]);
    const t = (result.translations[name] ?? [0, 0, 0]) as Vec3;
    const position: Vec3 = [
      -(rot[0][0] * t[0] + rot[1][0] * t[1] + rot[2][0] * t[2]),
      -(rot[0][1] * t[0] + rot[1][1] * t[1] + rot[2][1] * t[2]),
      -(rot[0][2] * t[0] + rot[1][2] * t[1] + rot[2][2] * t[2]),
    ];
    return {
      name,
      position,
      axisX: [rot[0][0], rot[0][1], rot[0][2]],
      axisY: [rot[1][0], rot[1][1], rot[1][2]],
      axisZ: [rot[2][0], rot[2][1], rot[2][2]],
    };
  });
}

function Frustum({
  pose,
  size,
  color,
  m,
  anchor,
}: {
  pose: CameraPose;
  size: number;
  color: string;
  m: number[][];
  anchor: boolean;
}) {
  const apex = mulMV(m, pose.position);
  const corner = (sx: number, sy: number): Vec3 =>
    mulMV(
      m,
      add(
        pose.position,
        add(
          scale(pose.axisZ, size),
          add(scale(pose.axisX, sx * size * 0.62), scale(pose.axisY, sy * size * 0.42)),
        ),
      ),
    );
  const corners = [corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)];
  return (
    <>
      {corners.map((c, i) => (
        <Line key={i} points={[apex, c]} color={color} lineWidth={anchor ? 2 : 1.2} />
      ))}
      <Line points={[...corners, corners[0]]} color={color} lineWidth={anchor ? 2.4 : 1.6} />
      {/* distanceFactor compensates the halved frustum size: labels stay legible. */}
      <Html position={apex} center distanceFactor={size * 20} style={{ pointerEvents: 'none' }}>
        <div
          style={{
            padding: '2px 7px',
            borderRadius: 10,
            background: 'rgba(9,9,11,0.78)',
            border: `1px solid ${color}`,
            color: '#e4e4e7',
            fontSize: 11,
            whiteSpace: 'nowrap',
          }}
        >
          {pose.name}
          {anchor ? ' · anchor' : ''}
        </div>
      </Html>
    </>
  );
}

// Tiny billboard letter at an axis tip — the RGB code alone was not readable.
function AxisLabel({ position, text, color }: { position: Vec3; text: string; color: string }) {
  return (
    <Html position={position} center style={{ pointerEvents: 'none' }}>
      <span style={{ color, fontSize: 11, fontWeight: 700, textShadow: '0 0 4px #000' }}>
        {text}
      </span>
    </Html>
  );
}

// Board outline + local xyz triad, derived from the quad's corner order (spec:
// c0->c1 = board +x, c0->c3 = board +y, z = x cross y). A single-ArUco marker's
// frame sits at its CENTER (cv2 convention) — anchor the triad on the centroid;
// a ChArUco board frame originates at its first chessboard corner (c0).
function BoardWithTriad({
  quad,
  m,
  centered,
}: {
  quad: number[][];
  m: number[][];
  centered: boolean;
}) {
  const corners = quad.map((c) => mulMV(m, c as Vec3));
  const x = unit(sub(corners[1], corners[0]));
  const y = unit(sub(corners[3], corners[0]));
  const z = unit(cross(x, y));
  const origin = centered
    ? scale(
        corners.reduce((acc, corner) => add(acc, corner), [0, 0, 0] as Vec3),
        1 / corners.length,
      )
    : corners[0];
  const len = norm(sub(corners[1], corners[0])) * 0.3;
  return (
    <>
      <Line points={[...corners, corners[0]]} color="#e4e4e7" lineWidth={1.6} />
      <Line points={[origin, add(origin, scale(x, len))]} color="#ef4444" lineWidth={2.4} />
      <Line points={[origin, add(origin, scale(y, len))]} color="#4ade80" lineWidth={2.4} />
      <Line points={[origin, add(origin, scale(z, len))]} color="#60a5fa" lineWidth={2.4} />
      <AxisLabel position={add(origin, scale(x, len * 1.35))} text="x" color="#ef4444" />
      <AxisLabel position={add(origin, scale(y, len * 1.35))} text="y" color="#4ade80" />
      <AxisLabel position={add(origin, scale(z, len * 1.35))} text="z" color="#60a5fa" />
    </>
  );
}

function WorldAxes({ size }: { size: number }) {
  const o: Vec3 = [0, 0, 0];
  return (
    <>
      <Line points={[o, [size, 0, 0]]} color="#ef4444" lineWidth={1.2} />
      <Line points={[o, [0, size, 0]]} color="#4ade80" lineWidth={1.2} />
      <Line points={[o, [0, 0, size]]} color="#60a5fa" lineWidth={1.2} />
      <AxisLabel position={[size * 1.14, 0, 0]} text="x" color="#ef4444" />
      <AxisLabel position={[0, size * 1.14, 0]} text="y" color="#4ade80" />
      <AxisLabel position={[0, 0, size * 1.14]} text="z" color="#60a5fa" />
    </>
  );
}

function GroupPoints({ positions, size }: { positions: Float32Array; size: number }) {
  return (
    <points key={positions.length + '-' + positions[0]}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial color="#fbbf24" size={size} sizeAttenuation />
    </points>
  );
}

export function ArrayReview({
  result,
  onResult,
  markerBoard = false,
}: {
  result: ExtrinsicResultPayload;
  onResult: (updated: ExtrinsicResultPayload) => void;
  markerBoard?: boolean;
}) {
  const [group, setGroup] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mutateError, setMutateError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // The World-frame controls are a 190px overlay — a third of a phone's 3D view. On
  // compact they collapse to a corner button and open on demand (ADR-0041); on desktop
  // they stay pinned open, where the space is free.
  const compact = useCompactLayout();
  const [controlsOpen, setControlsOpen] = useState(false);
  const showControls = !compact || controlsOpen;
  // Roomier hit targets once the panel is a deliberate touch surface (ADR-0041).
  const controlSize = compact ? 'sm' : 'compact-xs';
  const maxGroup = Math.max(0, result.group_count - 1);

  // Mutating review actions (spec 3d-extrinsic-review): reorient the stored world
  // frame / re-run the BA server-side, then swap in the updated result. Minimize
  // reports its before -> after RMSE — without it a converged re-fit looks dead.
  const mutate = async (action: () => Promise<ExtrinsicResultPayload>, report = false) => {
    setBusy(true);
    setMutateError(null);
    setNotice(null);
    try {
      const before = result.error;
      const updated = await action();
      onResult(updated);
      if (report) {
        // What the click COST in data, next to what it gained in RMSE: Minimize
        // trades observations for error, and the trade must be visible (ADR-0036;
        // it always re-filters from the full set, so this is not cumulative).
        const total = updated.observations_total ?? 0;
        const dropped = total - (updated.observations_used ?? 0);
        const filtered = dropped > 0 ? ` · ${dropped}/${total} obs dropped` : '';
        setNotice(
          Math.abs(before - updated.error) < 0.005
            ? `already converged · ${updated.error.toFixed(2)} px${filtered}`
            : `RMSE ${before.toFixed(2)} → ${updated.error.toFixed(2)} px${filtered}`,
        );
      }
    } catch (err) {
      setMutateError(err instanceof Error ? err.message : 'action failed');
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setGroup((g) => (g >= maxGroup ? 0 : g + 1)), 1000 / PLAY_FPS);
    return () => clearInterval(id);
  }, [playing, maxGroup]);

  const poses = cameraPoses(result);
  const sceneScale =
    poses.reduce((sum, pose) => sum + norm(pose.position), 0) / Math.max(1, poses.length - 1) || 10;
  const frustumSize = Math.max(0.25, sceneScale * 0.09);

  const current = Math.min(group, maxGroup);
  const groupPoints: number[] = [];
  result.point_groups.forEach((g, i) => {
    if (g === current) {
      const displayed = mulMV(VIEW_BASIS, result.points[i] as Vec3);
      groupPoints.push(displayed[0], displayed[1], displayed[2]);
    }
  });
  const positions = new Float32Array(groupPoints);
  const quad = result.board_quads[current] ?? null;

  const camDistance = sceneScale * 1.6;
  const initialCamera: Vec3 = [camDistance, camDistance * 0.7, camDistance];

  return (
    <Box
      style={{
        position: 'relative',
        height: '100%',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Box style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        <Canvas
          camera={{ position: initialCamera, up: VIEW_UP, fov: 50 }}
          style={{
            borderRadius: 'var(--mantine-radius-md)',
            background: '#16161b',
            height: '100%',
          }}
        >
          <ambientLight intensity={0.8} />
          <Bounds fit clip observe margin={1.25}>
            <WorldAxes size={sceneScale * 0.2} />
            {poses.map((pose, i) => (
              <Frustum
                key={pose.name}
                pose={pose}
                size={frustumSize}
                color={CAMERA_COLOR}
                m={VIEW_BASIS}
                anchor={i === 0}
              />
            ))}
            {positions.length > 0 && <GroupPoints positions={positions} size={sceneScale * 0.02} />}
            {quad && <BoardWithTriad quad={quad} m={VIEW_BASIS} centered={markerBoard} />}
          </Bounds>
          {/* Trackball, not Orbit: orbit clamps polar to [0, π] (blocks at the
              poles), which fights a reoriented world — free 360° tumbling. */}
          <TrackballControls makeDefault noPan rotateSpeed={3} />
        </Canvas>
        {showControls ? (
          <Box
            style={{
              position: 'absolute',
              top: 10,
              left: 10,
              zIndex: 2,
              padding: compact ? 10 : 8,
              borderRadius: 8,
              background: 'rgba(9,9,11,0.72)',
              backdropFilter: 'blur(6px)',
              border: '1px solid var(--rc-border)',
              width: compact ? 230 : 190,
            }}
          >
            <Group justify="space-between" wrap="nowrap" mb={6}>
              <Text fz="0.62rem" c="dark.3">
                World frame
              </Text>
              {compact && (
                <ActionIcon
                  size="sm"
                  variant="subtle"
                  color="gray"
                  aria-label="Hide world-frame controls"
                  onClick={() => setControlsOpen(false)}
                >
                  <IconX size={15} />
                </ActionIcon>
              )}
            </Group>
            {/* Single framing gesture (ADR-0026): origin on the board + its normal on
                the up axis, so a floor-laid board lands level in every export. */}
            <Button
              size={controlSize}
              fullWidth
              variant="light"
              leftSection={<IconCrosshair size={13} />}
              disabled={busy || quad === null}
              onClick={() => void mutate(() => orientExtrinsic({ op: 'set_frame', group: current }))}
            >
              Set frame on board
            </Button>
            <Group gap={4} mt={6} grow>
              {(['x', 'y', 'z'] as const).map((axis) => (
                <Box key={axis} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <Button
                    size={controlSize}
                    variant="default"
                    disabled={busy}
                    onClick={() =>
                      void mutate(() => orientExtrinsic({ op: 'rotate', axis, degrees: 90 }))
                    }
                  >
                    +{axis}
                  </Button>
                  <Button
                    size={controlSize}
                    variant="default"
                    disabled={busy}
                    onClick={() =>
                      void mutate(() => orientExtrinsic({ op: 'rotate', axis, degrees: -90 }))
                    }
                  >
                    −{axis}
                  </Button>
                </Box>
              ))}
            </Group>
            <Button
              size={controlSize}
              fullWidth
              mt={6}
              variant="light"
              color="violet"
              loading={busy}
              leftSection={<IconWand size={13} />}
              onClick={() => void mutate(() => minimizeExtrinsic(), true)}
            >
              Minimize (re-BA)
            </Button>
            {notice && (
              <Text fz="0.6rem" c="teal.4" mt={4}>
                {notice}
              </Text>
            )}
            {mutateError && (
              <Text fz="0.6rem" c="var(--rc-error)" mt={4}>
                {mutateError}
              </Text>
            )}
          </Box>
        ) : (
          // Compact + collapsed: a corner button that gives the 3D view back its space.
          <ActionIcon
            size="lg"
            variant="default"
            aria-label="World-frame controls"
            onClick={() => setControlsOpen(true)}
            style={{
              position: 'absolute',
              top: 10,
              left: 10,
              zIndex: 2,
              background: 'rgba(9,9,11,0.72)',
              backdropFilter: 'blur(6px)',
              border: '1px solid var(--rc-border)',
            }}
          >
            <IconAdjustments size={18} />
          </ActionIcon>
        )}
        {/* No convention selector (ADR-0026): the review shows the fixed physical
            frame; the convention is an export codec, chosen at the Export step. */}
      </Box>
      <Group mt="sm" gap="sm" wrap="nowrap">
        <ActionIcon
          variant="light"
          color="violet"
          size="lg"
          aria-label={playing ? 'Pause' : 'Play'}
          onClick={() => setPlaying((p) => !p)}
        >
          {playing ? <IconPlayerPauseFilled size={16} /> : <IconPlayerPlayFilled size={16} />}
        </ActionIcon>
        <Slider
          flex={1}
          min={0}
          max={maxGroup}
          value={current}
          onChange={(value) => {
            setPlaying(false);
            setGroup(value);
          }}
          label={null}
          color="violet"
          // "Set frame on board" marker (like the intrinsic trim marks): flags the
          // group whose board carries the world frame — persisted server-side,
          // kept through rotate/minimize, reset by a fresh solve.
          marks={
            result.framed_group != null && result.framed_group <= maxGroup
              ? [
                  {
                    value: result.framed_group,
                    label: (
                      <Text
                        fz="0.6rem"
                        c="var(--rc-accent-bright)"
                        style={{ whiteSpace: 'nowrap' }}
                      >
                        ⚑ frame
                      </Text>
                    ),
                  },
                ]
              : undefined
          }
        />
        <Text
          className="rc-tnum"
          fz="0.72rem"
          c="dark.2"
          w={110}
          ta="right"
          style={{ flex: 'none' }}
        >
          group {current} / {maxGroup}
        </Text>
      </Group>
    </Box>
  );
}
