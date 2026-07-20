import { describe, expect, it } from 'vitest';

import type { RootState } from '@/app/store';
import { selectStages } from '@/features/session/selectors';
import reducer, { rehydrateSession } from '@/features/session/sessionSlice';
import type { Board, CameraConfig, Session } from '@/transport/types';

function board(): Board {
  return {
    board_type: 'charuco',
    dictionary: 'DICT_5X5_100',
    columns: 8,
    rows: 5,
    marker_ratio: 0.75,
    marker_id: 0,
    square_size_mm: 40,
    marker_size_mm: 30,
    inverted: false,
  };
}

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
    cameras: [],
    intrinsic_board: null,
    extrinsic_board: null,
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
  it('no active session: every stage locked (ADR-0028, rail disabled)', () => {
    const stages = selectStages(stateWith(null));
    expect(stages.every((s) => s.status === 'locked')).toBe(true);
  });

  it('intrinsic board defined at camera_setup: boards complete, cameras active, intrinsic todo', () => {
    const stages = selectStages(
      stateWith(session({ step: 'camera_setup', intrinsic_board: board(), cameras: [camera()] })),
    );
    expect(stages[0]).toMatchObject({ id: 'boards', status: 'complete' });
    expect(stages[1]).toMatchObject({ id: 'cameras', status: 'active' });
    expect(stages[2]).toMatchObject({ id: 'intrinsic', status: 'todo' });
  });

  it('board undefined keeps cameras locked (board-first gating)', () => {
    const stages = selectStages(stateWith(session({ step: 'intrinsic_board' })));
    expect(stages[0]).toMatchObject({ id: 'boards', status: 'active' });
    expect(stages[1]).toMatchObject({ id: 'cameras', status: 'locked' });
  });

  it('at extrinsic_board_choice: boards active, cameras locked (extrinsic not skippable)', () => {
    const stages = selectStages(
      stateWith(session({ step: 'extrinsic_board_choice', intrinsic_board: board() })),
    );
    expect(stages.find((s) => s.id === 'boards')?.status).toBe('active');
    expect(stages.find((s) => s.id === 'cameras')?.status).toBe('locked');
  });

  it('all cameras extrinsic_done at extrinsic_capture: extrinsic active, export unlocked', () => {
    // No "Review 3D" stage: the 3D review lives inside the Extrinsics Result
    // sub-step, so a fully-solved array unlocks Export directly.
    const done = session({
      step: 'extrinsic_capture',
      mode: 'load-from-files',
      intrinsic_board: board(),
      cameras: [camera({ status: 'extrinsic_done' })],
    });
    const stages = selectStages(stateWith(done));
    expect(stages.find((s) => s.id === 'boards')?.status).toBe('complete');
    expect(stages.find((s) => s.id === 'cameras')?.status).toBe('complete');
    expect(stages.find((s) => s.id === 'intrinsic')?.status).toBe('complete');
    expect(stages.find((s) => s.id === 'extrinsic')?.status).toBe('active');
    expect(stages.find((s) => s.id === 'export')?.status).toBe('todo');
  });
});
