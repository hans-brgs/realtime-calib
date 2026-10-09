import { useEffect, useState } from 'react';

import { useAppSelector } from '@/app/hooks';
import {
  CAMERA_STATE_STALE_MS,
  type CameraCaptureState,
  selectCameraStateAt,
  selectCameraStates,
} from '@/features/telemetry/telemetrySlice';

const NO_STATES: Record<string, CameraCaptureState> = {};

// The last `camera_state` snapshot, or nothing once it is too old to trust. The
// service re-sends the whole rig every second, so silence means it stopped talking —
// and a camera it last reported in error may well be back: showing that stale error
// would be a lie in the other direction. Pure, so the cut-off is tested on its own.
export function freshCameraStates(
  states: Record<string, CameraCaptureState>,
  receivedAt: number | null,
  now: number,
): Record<string, CameraCaptureState> {
  if (receivedAt === null || now - receivedAt > CAMERA_STATE_STALE_MS) return NO_STATES;
  return states;
}

// Live capture health per camera track name (spec realtime-telemetry, #46). Re-reads
// the clock every second so a snapshot ages out even when no new message arrives.
export function useCameraHealth(): Record<string, CameraCaptureState> {
  const states = useAppSelector(selectCameraStates);
  const receivedAt = useAppSelector(selectCameraStateAt);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  // A snapshot newer than the last tick is fresh by definition; without this the
  // first message would wait up to a second for the clock to catch up.
  return freshCameraStates(states, receivedAt, Math.max(now, receivedAt ?? 0));
}

// Cameras currently failing, in track-name order — what the degradation banner lists.
export function failingCameras(
  states: Record<string, CameraCaptureState>,
): [string, CameraCaptureState][] {
  return Object.entries(states)
    .filter(([, health]) => health.state === 'error')
    .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }));
}

// Health dot colour per capture state. Unknown (no fresh snapshot yet) stays neutral:
// the old hardcoded green claimed a health nobody had measured.
export function healthColor(health: CameraCaptureState | undefined): string {
  switch (health?.state) {
    case 'live':
      return 'var(--rc-success)';
    case 'opening':
      return 'var(--rc-warning)';
    case 'error':
      return 'var(--rc-error)';
    default:
      return 'var(--mantine-color-dark-3)';
  }
}
