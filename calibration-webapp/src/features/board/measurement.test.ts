import { describe, expect, it } from 'vitest';

import { measurementOf } from './measurement';
import type { Board, Session, WizardStep } from '@/transport/types';

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
};

const MARKER: Board = { ...CHARUCO, board_type: 'aruco', marker_id: 8, marker_size_mm: 297.5 };

function session(step: WizardStep, boards: Partial<Session> = {}): Session {
  return {
    session_id: 'test',
    step,
    mode: 'new-realtime',
    cameras: [],
    intrinsic_board: CHARUCO,
    extrinsic_board: null,
    ...boards,
  };
}

describe('measurementOf', () => {
  it('leaves the field empty while Target Config is still being defined', () => {
    // The seeded 40 mm is a backend default, indistinguishable from a real
    // measurement once shown — so it must never reach the input.
    for (const step of ['entry', 'intrinsic_board', 'extrinsic_board_choice'] as WizardStep[]) {
      expect(measurementOf(session(step))).toBe('');
    }
    expect(measurementOf(null)).toBe('');
  });

  it('shows the stored size once the operator has confirmed the step', () => {
    // Past Target Config the persisted value IS their own measurement; blanking
    // it would force a needless re-measure on every revisit.
    expect(measurementOf(session('camera_setup'))).toBe(40);
    expect(measurementOf(session('export'))).toBe(40);
  });

  it('reads the separate extrinsic board when there is one', () => {
    const s = session('camera_setup', { extrinsic_board: MARKER });
    expect(measurementOf(s)).toBe(297.5);
  });

  it('falls back to the intrinsic board when the extrinsic step inherits it', () => {
    const s = session('camera_setup', {
      intrinsic_board: { ...CHARUCO, square_size_mm: 41.5 },
      extrinsic_board: null,
    });
    expect(measurementOf(s)).toBe(41.5);
  });

  it('takes the marker side, not the square, for a single-marker target', () => {
    // square_size_mm carries a leftover value on an ArUco board; the scale is
    // the marker side, matching board_unit_mm on the backend.
    const s = session('camera_setup', {
      extrinsic_board: { ...MARKER, square_size_mm: 40, marker_size_mm: 297.5 },
    });
    expect(measurementOf(s)).toBe(297.5);
  });
});
