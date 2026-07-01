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

const STAGES: StageDef[] = [
  { id: 'cameras', label: 'Caméras', steps: ['camera_setup'] },
  { id: 'boards', label: 'Boards', steps: ['intrinsic_board', 'extrinsic_board_choice'] },
  { id: 'intrinsic', label: 'Intrinsèque', steps: ['intrinsic_capture'] },
  { id: 'extrinsic', label: 'Extrinsèque', steps: ['extrinsic_capture'] },
  { id: 'review', label: 'Revue 3D', steps: ['review_3d'] },
  { id: 'export', label: 'Export', steps: ['export'] },
];

function isComplete(id: StageId, session: Session | null): boolean {
  if (session === null) {
    return false;
  }
  const cameras = session.cameras;
  switch (id) {
    case 'cameras':
      return cameras.length > 0;
    case 'intrinsic':
      return (
        cameras.length > 0 &&
        cameras.every((c) => c.status === 'intrinsic_done' || c.status === 'extrinsic_done')
      );
    case 'extrinsic':
      return cameras.length > 0 && cameras.every((c) => c.status === 'extrinsic_done');
    // boards / review / export: not modelled yet (data comes in later phases).
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
    prerequisitesMet = complete || active;
  }

  return stages;
}

// A "view" is what the rail can land on: the dashboard (session entry) or one of the
// six wizard stages. Navigation between completed stages is free (completion-driven,
// spec wizard-navigation); it does not mutate the persisted FSM step.
export type ViewId = StageId | 'session';

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
