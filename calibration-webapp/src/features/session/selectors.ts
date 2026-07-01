// Wizard stages with completion-driven status (spec wizard-navigation):
// status is derived from session DATA, not from a sequential cursor.

import type { RootState } from '@/app/store';
import type { Session, WizardStep } from '@/transport/types';

export type StageId = 'cameras' | 'boards' | 'intrinsic' | 'extrinsic' | 'review' | 'export';
export type StageStatus = 'complete' | 'active' | 'todo' | 'locked';

export interface Stage {
  id: StageId;
  label: string;
  status: StageStatus;
}

interface StageDef {
  id: StageId;
  label: string;
  steps: WizardStep[];
}

// Board definition comes first so the operator can print early, before wiring
// cameras (ADR-0020 workflow).
const STAGES: StageDef[] = [
  { id: 'boards', label: 'Target Config', steps: ['intrinsic_board', 'extrinsic_board_choice'] },
  { id: 'cameras', label: 'Camera Setup', steps: ['camera_setup'] },
  { id: 'intrinsic', label: 'Intrinsics', steps: ['intrinsic_capture'] },
  { id: 'extrinsic', label: 'Extrinsics', steps: ['extrinsic_capture'] },
  { id: 'review', label: 'Review 3D', steps: ['review_3d'] },
  { id: 'export', label: 'Export', steps: ['export'] },
];

function isComplete(id: StageId, session: Session | null): boolean {
  if (session === null) {
    return false;
  }
  const cameras = session.cameras;
  switch (id) {
    case 'boards':
      // Target Config complete once the intrinsic board is defined (the extrinsic
      // board inherits it by default) — this unlocks Camera Setup (board-first).
      return session.intrinsic_board !== null;
    case 'cameras':
      return cameras.length > 0;
    case 'intrinsic':
      return (
        cameras.length > 0 &&
        cameras.every((c) => c.status === 'intrinsic_done' || c.status === 'extrinsic_done')
      );
    case 'extrinsic':
      return cameras.length > 0 && cameras.every((c) => c.status === 'extrinsic_done');
    // review / export: not modelled yet (data comes in later phases).
    default:
      return false;
  }
}

export function selectStages(state: RootState): Stage[] {
  const session = state.session.session;
  const step = session?.step ?? 'entry';
  const stages: Stage[] = [];
  let prerequisitesMet = true;

  for (const def of STAGES) {
    const active = def.steps.includes(step);
    const complete = isComplete(def.id, session);
    let status: StageStatus;
    if (active) {
      status = 'active';
    } else if (complete) {
      status = 'complete';
    } else if (prerequisitesMet) {
      status = 'todo';
    } else {
      status = 'locked';
    }
    stages.push({ id: def.id, label: def.label, status });
    // Strict linear progression: a stage unlocks only once the previous one is
    // *complete* (not merely active). This enforces board-first — Camera Setup
    // stays locked until the intrinsic board is defined (ADR-0020).
    prerequisitesMet = complete;
  }

  return stages;
}

// A "view" is what the rail can land on: the dashboard (session entry) or one of the
// six wizard stages. Navigation between completed stages is free (completion-driven,
// spec wizard-navigation); it does not mutate the persisted FSM step.
export type ViewId = StageId | 'session';

// Navigation targets include the rail views plus transient screens reached outside
// the rail (e.g. the Load-from-files entry from the dashboard). 'load' is never
// derived from a persisted step — it is a volatile sub-flow of the session entry.
export type NavTarget = ViewId | 'load';

export function stepToView(step: WizardStep): ViewId {
  if (step === 'entry') {
    return 'session';
  }
  const def = STAGES.find((s) => s.steps.includes(step));
  return def ? def.id : 'session';
}

export function selectActiveView(state: RootState): ViewId {
  return stepToView(state.session.session?.step ?? 'entry');
}
