import { measurementOf } from '@/features/board/measurement';
import type { Board, Session } from '@/transport/types';

/**
 * Where a Target Config tab stands against what the service stores: never saved,
 * saved and unchanged, or edited since. The form is local state, so without this
 * an edit (or a whole board) could be left unsaved with nothing on screen saying
 * so — the rig test of 2026-10-10 went to Intrinsics without an intrinsic board.
 */
export type DraftStatus = 'unsaved' | 'saved' | 'edited';

/**
 * The fields the operator sets, per board type. Sizes the service derives are left
 * out: a ChArUco marker size is `marker_ratio × square` and is written rounded, so
 * comparing it would call an untouched board edited.
 */
function sameGeometry(a: Board, b: Board): boolean {
  if (a.board_type !== b.board_type || a.dictionary !== b.dictionary) return false;
  if (a.inverted !== b.inverted) return false;
  if (a.board_type === 'charuco') {
    return (
      a.columns === b.columns &&
      a.rows === b.rows &&
      a.marker_ratio === b.marker_ratio &&
      a.legacy_pattern === b.legacy_pattern
    );
  }
  return a.marker_id === b.marker_id;
}

export function intrinsicStatus(draft: Board, session: Session | null): DraftStatus {
  const saved = session?.intrinsic_board;
  if (!saved) return 'unsaved';
  return sameGeometry(draft, saved) ? 'saved' : 'edited';
}

/**
 * The extrinsic tab: the inherit-or-separate choice, the measurement, and — for a
 * separate board — its geometry. An inherited board's geometry is the intrinsic
 * one, copied by the service, so it is not compared here.
 */
export function extrinsicStatus(
  draft: Board,
  different: boolean,
  measurement: number | '',
  session: Session | null,
): DraftStatus {
  const saved = session?.extrinsic_board;
  if (!session || !saved) return 'unsaved';
  const savedDifferent = !session.extrinsic_inherited;
  if (different !== savedDifferent || measurement !== measurementOf(session)) return 'edited';
  return !different || sameGeometry(draft, saved) ? 'saved' : 'edited';
}
