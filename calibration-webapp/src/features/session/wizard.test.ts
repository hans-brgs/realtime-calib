import { describe, expect, it } from 'vitest';

import type { RootState } from '@/app/store';
import { selectStages } from '@/features/session/selectors';
import reducer, { rehydrateSession } from '@/features/session/sessionSlice';
import type { CameraConfig, Session } from '@/transport/types';

function camera(overrides: Partial<CameraConfig> = {}): CameraConfig {
  return {
    index: 0,
    name: 'cam_0',
    prefix: 'cam',
    device_path: '/dev/v4l/by-path/a',
    device_node: '/dev/video0',
    width: 1920,
    height: 1080,
    resize_factor: 1,
    fps: 30,
    status: 'configured',
    ...overrides,
  };
}

function session(overrides: Partial<Session> = {}): Session {
  return {
    session_id: 'default',
    step: 'camera_setup',
    mode: 'new-realtime',
    intrinsic_fps: 30,
    optimization_strategy: 'coverage-aware',
    cameras: [],
    ...overrides,
  };
}

function stateWith(s: Session | null): RootState {
  return { session: { session: s, status: 'ready', error: null } } as unknown as RootState;
}

describe('sessionSlice', () => {
  it('stores the session on rehydrate fulfilled', () => {
    const next = reducer(undefined, rehydrateSession.fulfilled(session(), 'reqid', undefined));
    expect(next.status).toBe('ready');
    expect(next.session?.session_id).toBe('default');
  });
});

describe('selectStages (completion-driven)', () => {
  it('no session: cameras is todo, the rest locked', () => {
    const stages = selectStages(stateWith(null));
    expect(stages[0]).toMatchObject({ id: 'cameras', status: 'todo' });
    expect(stages[1].status).toBe('locked');
    expect(stages[2].status).toBe('locked');
  });

  it('configured cameras at camera_setup: cameras active, boards todo, intrinsic locked', () => {
    const stages = selectStages(stateWith(session({ cameras: [camera()] })));
    expect(stages[0]).toMatchObject({ id: 'cameras', status: 'active' });
    expect(stages[1]).toMatchObject({ id: 'boards', status: 'todo' });
    expect(stages[2]).toMatchObject({ id: 'intrinsic', status: 'locked' });
  });

  it('load-from-files at review_3d with all done: stages complete, review active', () => {
    const done = session({
      step: 'review_3d',
      mode: 'load-from-files',
      cameras: [camera({ status: 'extrinsic_done' })],
    });
    const stages = selectStages(stateWith(done));
    expect(stages.find((s) => s.id === 'cameras')?.status).toBe('complete');
    expect(stages.find((s) => s.id === 'intrinsic')?.status).toBe('complete');
    expect(stages.find((s) => s.id === 'extrinsic')?.status).toBe('complete');
    expect(stages.find((s) => s.id === 'review')?.status).toBe('active');
  });
});
