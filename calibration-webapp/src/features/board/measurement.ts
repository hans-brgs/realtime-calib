import type { Board, Session } from '@/transport/types';

/**
 * Seed for the printed-size field, or `''` to leave it blank.
 *
 * Never seeded from a default: a pre-filled 40 mm is indistinguishable from a
 * measurement the operator actually took, and that number scales every exported
 * distance — the silent failure mode this guards against.
 *
 * Reading the extrinsic board alone is what makes that safe, and it needs no
 * wizard-step guard to be: that board only exists once the extrinsic step has
 * been confirmed (inheriting materializes a copy of the intrinsic geometry
 * there), so a size found here IS the operator's own measurement. Before that —
 * and on a session predating the materialization — there is nothing to seed
 * from. The scale is the square side for ChArUco and the marker side for a
 * single marker, like `board_unit_mm`.
 */
export function measurementOf(session: Session | null): number | '' {
  const board = session?.extrinsic_board;
  if (!board) {
    return '';
  }
  return board.board_type === 'charuco' ? board.square_size_mm : board.marker_size_mm;
}

/**
 * Fold the measured size into the board about to be saved.
 *
 * The measurement is held outside the board objects while the operator types, so
 * an untouched field can never pass for a value and clearing it to retype cannot
 * leave a zero size on a board.
 */
export function withMeasurement(board: Board, mm: number): Board {
  return board.board_type === 'charuco'
    ? { ...board, square_size_mm: mm }
    : { ...board, marker_size_mm: mm };
}
