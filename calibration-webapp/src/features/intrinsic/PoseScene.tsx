import { Bounds, Line, OrbitControls } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';

// Results 3D pose view (ADR-0022): the calibrated boards floating around the camera at
// their recovered poses (rvec/tvec → 4 outline corners, in board-square units). Single
// camera + known planar target + K ⇒ full metric 6-DoF pose, so this is exact, not a
// guess. Camera sits at the origin; boards are the keyframes that constrained the solve.
type Vec3 = [number, number, number];

const CAMERA_COLOR = '#a78bfa';
const BOARD_COLOR = '#4ade80';

function BoardOutline({ corners }: { corners: Vec3[] }) {
  return <Line points={[...corners, corners[0]]} color={BOARD_COLOR} lineWidth={1.5} />;
}

function CameraFrustum({ size }: { size: number }) {
  const w = size * 0.7;
  const near: Vec3[] = [
    [-w, -w, size],
    [w, -w, size],
    [w, w, size],
    [-w, w, size],
  ];
  const apex: Vec3 = [0, 0, 0];
  return (
    <>
      {near.map((corner, i) => (
        <Line key={i} points={[apex, corner]} color={CAMERA_COLOR} lineWidth={1} />
      ))}
      <Line points={[...near, near[0]]} color={CAMERA_COLOR} lineWidth={1.5} />
    </>
  );
}

export function PoseScene({ quads }: { quads: number[][][] }) {
  const boards = quads.map((q) => q.map((p) => [p[0], p[1], p[2]] as Vec3));
  const meanDepth =
    boards.length > 0
      ? boards.reduce((sum, b) => sum + b.reduce((a, p) => a + p[2], 0) / b.length, 0) / boards.length
      : 10;
  const frustum = Math.max(1, Math.abs(meanDepth) * 0.15);

  return (
    <Canvas
      camera={{ position: [meanDepth * 1.4, -meanDepth * 0.9, -meanDepth * 0.6], fov: 50 }}
      style={{ borderRadius: 'var(--mantine-radius-md)', background: '#16161b', height: '100%' }}
    >
      <ambientLight intensity={0.8} />
      <Bounds fit clip observe margin={1.3}>
        <CameraFrustum size={frustum} />
        {boards.map((corners, i) => (
          <BoardOutline key={i} corners={corners} />
        ))}
      </Bounds>
      <OrbitControls makeDefault enablePan={false} />
    </Canvas>
  );
}
