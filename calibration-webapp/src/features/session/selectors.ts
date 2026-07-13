// Wizard stages with completion-driven status (spec wizard-navigation):
// status is derived from session DATA, not from a sequential cursor.

import type { RootState } from '@/app/store';
import type { Session, WizardStep } from '@/transport/types';

export type StageId = 'cameras' | 'boards' | 'intrinsic' | 'extrinsic' | 'export';
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
// cameras (ADR-0020 workflow). No "Review 3D" stage: the 3D review IS the Result
// sub-step of Extrinsics (spec 3d-extrinsic-review / extrinsic-calibration-flow).
const STAGES: StageDef[] = [
  { id: 'boards', label: 'Target Config', steps: ['intrinsic_board', 'extrinsic_board_choice'] },
  { id: 'cameras', label: 'Camera Setup', steps: ['camera_setup'] },
  { id: 'intrinsic', label: 'Intrinsics', steps: ['intrinsic_capture'] },
  { id: 'extrinsic', label: 'Extrinsics', steps: ['extrinsic_capture'] },
  { id: 'export', label: 'Export', steps: ['export'] },
];

function isComplete(id: StageId, session: Session | null): boolean {
  if (session === null) {
    return false;
  }
  const cameras = session.cameras;
  switch (id) {
    case 'boards':
      // Target Config completes only when BOTH board choices are locked in: the
      // intrinsic board is defined AND the extrinsic choice is confirmed (the persisted
      // step advanced past the boards stage). Keeps Camera Setup locked so the extrinsic
      // board can't be skipped via the rail (board-first, spec wizard-navigation).
      return (
        session.intrinsic_board !== null &&
        session.step !== 'intrinsic_board' &&
        session.step !== 'extrinsic_board_choice'
      );
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
  // No active session (ADR-0028): prerequisites start unmet so EVERY stage locks —
  // the operator must first create or open a session from the dashboard.
  let prerequisitesMet = session !== null;

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

// Navigation targets are exactly the rail views: session entry sub-flows (create /
// import-from-files) are dashboard modals (ADR-0031), not views of their own.
export type NavTarget = ViewId;

function stepToView(step: WizardStep): ViewId {
  if (step === 'entry') {
    return 'session';
  }
  const def = STAGES.find((s) => s.steps.includes(step));
  return def ? def.id : 'session';
}

export function selectActiveView(state: RootState): ViewId {
  return stepToView(state.session.session?.step ?? 'entry');
}
