import { describe, expect, it } from 'vitest';

import { extrinsicStatus, intrinsicStatus } from './boardDraft';
import type { Board, Session } from '@/transport/types';

const CHARUCO: Board = {
  board_type: 'charuco',
  dictionary: 'DICT_4X4_100',
  columns: 7,
  rows: 9,
  marker_ratio: 0.75,
  marker_id: 0,
  square_size_mm: 40,
  marker_size_mm: 30,
  inverted: false,
  legacy_pattern: false,
};

const MARKER: Board = { ...CHARUCO, board_type: 'aruco', marker_id: 8, marker_size_mm: 297 };

function session(boards: Partial<Session>): Session {
  return {
    session_id: 'test',
    step: 'extrinsic_board_choice',
    mode: 'new-realtime',
    cameras: [],
    intrinsic_board: null,
    extrinsic_board: null,
    ...boards,
  };
}

describe('intrinsicStatus', () => {
  it('is unsaved until the service holds an intrinsic board', () => {
    expect(intrinsicStatus(CHARUCO, session({}))).toBe('unsaved');
    expect(intrinsicStatus(CHARUCO, null)).toBe('unsaved');
  });

  it('is saved when the draft matches, edited when a geometry field changes', () => {
    const saved = session({ intrinsic_board: CHARUCO });
    expect(intrinsicStatus({ ...CHARUCO }, saved)).toBe('saved');
    expect(intrinsicStatus({ ...CHARUCO, rows: 8 }, saved)).toBe('edited');
    expect(intrinsicStatus({ ...CHARUCO, legacy_pattern: true }, saved)).toBe('edited');
  });

  it('ignores the derived marker size the service rounds', () => {
    const saved = session({ intrinsic_board: { ...CHARUCO, marker_size_mm: 30.0001 } });
    expect(intrinsicStatus(CHARUCO, saved)).toBe('saved');
  });
});

describe('extrinsicStatus', () => {
  const inherited = session({
    intrinsic_board: CHARUCO,
    extrinsic_board: { ...CHARUCO, square_size_mm: 42.5 },
    extrinsic_inherited: true,
  });

  it('is unsaved until an extrinsic board is stored', () => {
    expect(extrinsicStatus(CHARUCO, false, 42.5, session({ intrinsic_board: CHARUCO }))).toBe(
      'unsaved',
    );
  });

  it('tracks the inherit choice and the measurement', () => {
    expect(extrinsicStatus(CHARUCO, false, 42.5, inherited)).toBe('saved');
    expect(extrinsicStatus(CHARUCO, false, 43, inherited)).toBe('edited');
    expect(extrinsicStatus(CHARUCO, false, '', inherited)).toBe('edited');
    expect(extrinsicStatus(MARKER, true, 42.5, inherited)).toBe('edited');
  });

  it('compares a separate board geometry too', () => {
    const separate = session({
      intrinsic_board: CHARUCO,
      extrinsic_board: MARKER,
      extrinsic_inherited: false,
    });
    expect(extrinsicStatus(MARKER, true, 297, separate)).toBe('saved');
    expect(extrinsicStatus({ ...MARKER, marker_id: 9 }, true, 297, separate)).toBe('edited');
    expect(extrinsicStatus(MARKER, true, 298, separate)).toBe('edited');
  });
});
