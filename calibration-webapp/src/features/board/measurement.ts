import type { Board, Session, WizardStep } from '@/transport/types';

// Target Config is the last step at which no human has confirmed a board yet, so
// up to here every board number on screen is a seed — a backend default or a
// previous session's value.
const TARGET_CONFIG_PENDING: WizardStep[] = ['entry', 'intrinsic_board', 'extrinsic_board_choice'];

/**
 * Seed for the printed-size field, or `''` to leave it blank.
 *
 * Never seeded from a default: a pre-filled 40 mm is indistinguishable from a
 * measurement the operator actually took, and that number scales every exported
 * distance — the silent failure mode this guards against. The field stays empty
 * until Target Config has been confirmed at least once; past that point the
 * stored value IS their own measurement, so blanking it would force a needless
 * re-measure on every revisit.
 *
 * Which board is read mirrors the backend's `effective_extrinsic_board`: the
 * explicit extrinsic board when there is one, the inherited intrinsic board
 * otherwise. The scale is the square side for ChArUco and the marker side for a
 * single marker, like `board_unit_mm`.
 */
export function measurementOf(session: Session | null): number | '' {
  if (!session || TARGET_CONFIG_PENDING.includes(session.step)) {
    return '';
  }
  const board = session.extrinsic_board ?? session.intrinsic_board;
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
