import { Bounds, Grid, Html, Line, TrackballControls, useBounds } from '@react-three/drei';
import { Canvas, useThree } from '@react-three/fiber';
import { DoubleSide } from 'three';
import {
  ActionIcon,
  Box,
  Button,
  Group,
  SegmentedControl,
  Slider,
  Text,
  Tooltip,
} from '@mantine/core';
import {
  IconAdjustments,
  IconCrosshair,
  IconFocusCentered,
  IconMapPin,
  IconPlayerPauseFilled,
  IconPlayerPlayFilled,
  IconWand,
  IconX,
} from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { useCompactLayout } from '@/components/layout/useCompactLayout';
import { ReferenceAlignModal } from '@/features/review3d/ReferenceAlignModal';
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

// One hue for the rig (cameras read by label), the anchor a brighter step of it.
const CAMERA_COLOR = '#a78bfa';
const ANCHOR_COLOR = '#ddd6fe';
const BOARD_COLOR = '#e4e4e7';
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

// A flat translucent quad (two triangles) through four corners, drawn from both sides:
// the image plane of a frustum, the board face.
function QuadFace({
  corners,
  color,
  opacity,
}: {
  corners: Vec3[];
  color: string;
  opacity: number;
}) {
  const [a, b, c, d] = corners;
  const positions = new Float32Array([...a, ...b, ...c, ...a, ...c, ...d]);
  return (
    <mesh key={positions.join(',')}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <meshBasicMaterial
        color={color}
        transparent
        opacity={opacity}
        side={DoubleSide}
        depthWrite={false}
      />
    </mesh>
  );
}

function Frustum({
  pose,
  size,
  m,
  anchor,
}: {
  pose: CameraPose;
  size: number;
  m: number[][];
  anchor: boolean;
}) {
  const color = anchor ? ANCHOR_COLOR : CAMERA_COLOR;
  const apex = mulMV(m, pose.position);
  // A point of the image plane, at depth `size`: sx, sy in [-1, 1] across its width
  // and height (OpenCV axes: +x right, +y DOWN in the image).
  const at = (sx: number, sy: number): Vec3 =>
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
  const corners = [at(-1, -1), at(1, -1), at(1, 1), at(-1, 1)];
  // A small triangle on the TOP edge (image -y): which way the camera is rolled reads
  // at a glance, as on most calibration viewers.
  const up = [at(-0.3, -1.12), at(0, -1.5), at(0.3, -1.12)];
  return (
    <>
      {corners.map((c, i) => (
        <Line key={i} points={[apex, c]} color={color} lineWidth={anchor ? 1.8 : 1.2} />
      ))}
      <Line points={[...corners, corners[0]]} color={color} lineWidth={anchor ? 2.2 : 1.6} />
      <QuadFace corners={corners} color={color} opacity={anchor ? 0.22 : 0.14} />
      <Line points={[...up, up[0]]} color={color} lineWidth={1.4} />
      {/* Fixed screen-size tag. No distanceFactor: it scales the label like a 3D
          object, so the nearest camera wore a giant name plate covering its own
          frustum — worst on the small mobile canvas. And no `center`: anchored on
          the apex it hid the very point it labels; the screen-space offset parks it
          up-right of the camera, whatever the scene orientation. zIndexRange [1,0]:
          above the canvas, below the World-frame panel (z 2) and every app overlay. */}
      <Html position={apex} zIndexRange={[1, 0]} style={{ pointerEvents: 'none' }}>
        <div
          style={{
            transform: 'translate(9px, calc(-100% - 6px))',
            padding: '1px 6px',
            borderRadius: 8,
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

// Billboard letter at an axis tip — the RGB code alone was not readable.
function AxisLabel({
  position,
  text,
  color,
  size = 11,
}: {
  position: Vec3;
  text: string;
  color: string;
  size?: number;
}) {
  return (
    <Html position={position} center zIndexRange={[1, 0]} style={{ pointerEvents: 'none' }}>
      <span style={{ color, fontSize: size, fontWeight: 700, textShadow: '0 0 4px #000' }}>
        {text}
      </span>
    </Html>
  );
}

// Board face + outline + its local triad, derived from the quad's corner order (spec:
// c0->c1 = board +x, c0->c3 = board +y, z = x cross y). A single-ArUco marker's
// frame sits at its CENTER (cv2 convention) — anchor the triad on the centroid;
// a ChArUco board frame originates at its first chessboard corner (c0).
//
// The triad shows in the "Board" axes mode only, the world axes in "World": one frame
// at a time. Both at once overlapped, and on the group the world was framed on with
// crossed colours (the world is that board's frame in another axis order: x, normal,
// board y once laid on the floor). Lowercase letters for the board, X/Y/Z for the world.
function BoardWithTriad({
  quad,
  m,
  centered,
  triad,
}: {
  quad: number[][];
  m: number[][];
  centered: boolean;
  triad: boolean;
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
  const len = norm(sub(corners[1], corners[0])) * 0.6;
  return (
    <>
      <QuadFace corners={corners} color={BOARD_COLOR} opacity={0.16} />
      <Line points={[...corners, corners[0]]} color={BOARD_COLOR} lineWidth={1.6} />
      {triad && (
        <>
          <Line points={[origin, add(origin, scale(x, len))]} color="#ef4444" lineWidth={2.2} />
          <Line points={[origin, add(origin, scale(y, len))]} color="#4ade80" lineWidth={2.2} />
          <Line points={[origin, add(origin, scale(z, len))]} color="#60a5fa" lineWidth={2.2} />
          <AxisLabel position={add(origin, scale(x, len * 1.3))} text="x" color="#ef4444" />
          <AxisLabel position={add(origin, scale(y, len * 1.3))} text="y" color="#4ade80" />
          <AxisLabel position={add(origin, scale(z, len * 1.3))} text="z" color="#60a5fa" />
        </>
      )}
    </>
  );
}

// Back to the home view on demand: upright, from the default corner, fitted on the
// rig. Bounds' own reset keeps the camera's current direction and roll, so after a
// free tumble "recenter" stayed upside down.
function HomeView({ trigger, position }: { trigger: number; position: Vec3 }) {
  const bounds = useBounds();
  const camera = useThree((state) => state.camera);
  useEffect(() => {
    if (trigger === 0) return;
    camera.up.set(VIEW_UP[0], VIEW_UP[1], VIEW_UP[2]);
    camera.position.set(position[0], position[1], position[2]);
    bounds.refresh().clip().fit();
    // Only on a new request, not when the poses re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trigger]);
  return null;
}

// The world frame: solid axes with X/Y/Z letters.
function WorldAxes({ size }: { size: number }) {
  const o: Vec3 = [0, 0, 0];
  return (
    <>
      <Line points={[o, [size, 0, 0]]} color="#ef4444" lineWidth={2.4} />
      <Line points={[o, [0, size, 0]]} color="#4ade80" lineWidth={2.4} />
      <Line points={[o, [0, 0, size]]} color="#60a5fa" lineWidth={2.4} />
      <AxisLabel position={[size * 1.12, 0, 0]} text="X" color="#ef4444" size={13} />
      <AxisLabel position={[0, size * 1.12, 0]} text="Y" color="#4ade80" size={13} />
      <AxisLabel position={[0, 0, size * 1.12]} text="Z" color="#60a5fa" size={13} />
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
  // The World-frame controls are a 190px overlay over the 3D view, foldable to a corner
  // button. Folded by default in compact, where they would cover a third of a phone's
  // view (ADR-0041); open by default on desktop. `null` = the layout's default, until
  // the operator chooses.
  const compact = useCompactLayout();
  const [controlsOpen, setControlsOpen] = useState<boolean | null>(null);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [recenters, setRecenters] = useState(0);
  // Which frame the scene draws: the world axes, or the scrubbed board's own triad.
  const [axes, setAxes] = useState<'world' | 'board'>('world');
  const showControls = controlsOpen ?? !compact;
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
        // The before -> after RMSE is the only genuinely TRANSIENT half of the
        // report: a delta exists in the moment of the click and nowhere in the
        // persisted result. What the click cost in data now lives permanently in
        // the Array result panel's observation ratio, so repeating it here would
        // be the same fact twice — and the transient copy would be the one that
        // disappears on the next action.
        setNotice(
          Math.abs(before - updated.error) < 0.005
            ? `already converged · ${updated.error.toFixed(2)} px`
            : `RMSE ${before.toFixed(2)} → ${updated.error.toFixed(2)} px`,
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

  // Floor grid: about ten cells across the rig, on a round power of ten of the units.
  const gridCell = 10 ** Math.round(Math.log10(Math.max(sceneScale / 10, 1e-3)));
  // Re-frame the view when the solve changes (reorientation, Minimize), not on scrub.
  const solveRevision = result.cameras
    .map((name) =>
      [...(result.rotations[name] ?? []), ...(result.translations[name] ?? [])].join(','),
    )
    .join('|');

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
      <ReferenceAlignModal
        opened={referenceOpen}
        onClose={() => setReferenceOpen(false)}
        onResult={onResult}
      />
      {/* overflow hidden: a label whose 3D anchor projects outside the view is an
          absolutely-positioned DOM node, not a canvas pixel — unclipped it paints
          over whatever surrounds the scene. Radius matches the canvas corner. */}
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          position: 'relative',
          overflow: 'hidden',
          borderRadius: 'var(--mantine-radius-md)',
        }}
      >
        <Canvas
          camera={{ position: initialCamera, up: VIEW_UP, fov: 50 }}
          style={{
            borderRadius: 'var(--mantine-radius-md)',
            background: '#16161b',
            height: '100%',
          }}
        >
          <ambientLight intensity={0.8} />
          {/* The view is framed ONCE per solve, on the rig (cameras + world axes):
              no `observe`, which re-framed on every canvas resize (panel or rail
              fold, window) and threw the operator's rotation away. The key re-frames
              after a reorientation or Minimize; Recenter goes back to the home view.
              The board and the points of the scrubbed group stay out of the fit: the
              view does not follow the scrubber. */}
          <Bounds key={solveRevision} fit clip margin={1.6}>
            <HomeView trigger={recenters} position={initialCamera} />
            {axes === 'world' && <WorldAxes size={sceneScale * 0.22} />}
            {poses.map((pose, i) => (
              <Frustum
                key={pose.name}
                pose={pose}
                size={frustumSize}
                m={VIEW_BASIS}
                anchor={i === 0}
              />
            ))}
          </Bounds>
          {/* Floor grid on the world XZ plane, only once the world is framed on a
              board (or aligned on a reference): before that the world is the anchor
              camera's frame and y = 0 a tilted plane through that camera. Faded
              around the origin (fadeFrom 0), not around the viewer. */}
          {(result.framed_group != null || result.alignment) && (
            <Grid
              args={[2, 2]}
              infiniteGrid
              fadeFrom={0}
              side={DoubleSide}
              cellSize={gridCell}
              sectionSize={gridCell * 5}
              cellThickness={0.6}
              sectionThickness={1}
              cellColor="#24242b"
              sectionColor="#34343d"
              fadeDistance={sceneScale * 6}
              fadeStrength={1.5}
            />
          )}
          {positions.length > 0 && <GroupPoints positions={positions} size={sceneScale * 0.015} />}
          {quad && (
            <BoardWithTriad
              quad={quad}
              m={VIEW_BASIS}
              centered={markerBoard}
              triad={axes === 'board'}
            />
          )}
          {/* Trackball, not Orbit: orbit clamps polar to [0, π] (blocks at the
              poles), which fights a reoriented world — free 360° tumbling. Static:
              no inertia, the view stops where the drag stops (the inertia also
              re-applied the last delta whenever the pointer paused mid-drag: the
              "jumps"). Static mode applies a zoom step once instead of ~5x with
              decay, hence the higher zoom speed. Pan on: two fingers translate (and
              pinch), the right mouse button too; Recenter brings the rig back. */}
          <TrackballControls
            makeDefault
            rotateSpeed={1.6}
            zoomSpeed={5}
            panSpeed={0.8}
            staticMoving
          />
        </Canvas>
        <Group
          gap={8}
          wrap="nowrap"
          style={{ position: 'absolute', top: 10, right: 10, zIndex: 2 }}
        >
          {/* Which frame is drawn: the world axes or the scrubbed board's triad. */}
          <SegmentedControl
            size="xs"
            aria-label="Axes shown"
            value={axes}
            onChange={(v) => setAxes(v as 'world' | 'board')}
            data={[
              { label: 'World', value: 'world' },
              { label: 'Board', value: 'board' },
            ]}
            styles={{
              root: { background: 'rgba(9,9,11,0.72)', backdropFilter: 'blur(6px)' },
            }}
          />
          <Tooltip label="Recenter the view" position="left" withArrow>
            <ActionIcon
              size="lg"
              variant="default"
              aria-label="Recenter the view"
              onClick={() => setRecenters((n) => n + 1)}
              style={{
                background: 'rgba(9,9,11,0.72)',
                backdropFilter: 'blur(6px)',
                border: '1px solid var(--rc-border)',
              }}
            >
              <IconFocusCentered size={18} />
            </ActionIcon>
          </Tooltip>
        </Group>
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
              <ActionIcon
                size="sm"
                variant="subtle"
                color="gray"
                aria-label="Hide world-frame controls"
                onClick={() => setControlsOpen(false)}
              >
                <IconX size={15} />
              </ActionIcon>
            </Group>
            {/* Single framing gesture (ADR-0026): origin on the board + its normal on
                the up axis, so a floor-laid board lands level in every export. */}
            <Button
              size={controlSize}
              fullWidth
              variant="light"
              leftSection={<IconCrosshair size={13} />}
              disabled={busy || quad === null}
              onClick={() =>
                void mutate(() => orientExtrinsic({ op: 'set_frame', group: current }))
              }
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
            {/* Re-align on a previous calibration of the room (ADR-0061). */}
            <Button
              size={controlSize}
              fullWidth
              mt={6}
              variant="default"
              leftSection={<IconMapPin size={13} />}
              disabled={busy}
              onClick={() => setReferenceOpen(true)}
            >
              {result.alignment ? 'Reference ✓' : 'Align on reference'}
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
          // Folded: a corner button that gives the 3D view back its space.
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
        {/* Worst-case width (tabular digits): no "group" prefix, no flat 110px — but
            fully natural width made the slider breathe as the counter gains digits. */}
        <Text
          className="rc-tnum"
          fz="0.72rem"
          c="dark.2"
          ta="right"
          style={{ flex: 'none', minWidth: `${`${maxGroup} / ${maxGroup}`.length}ch` }}
        >
          {current} / {maxGroup}
        </Text>
      </Group>
    </Box>
  );
}
