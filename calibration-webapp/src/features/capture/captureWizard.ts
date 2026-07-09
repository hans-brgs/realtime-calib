// Pure finite-state machine for the per-recording capture sub-wizard shared by the
// intrinsic and extrinsic screens: capture -> prepare -> computing -> review. Kept as a
// plain reducer (no React) so the transitions — including the ADR-0019 overwrite guard —
// are unit-tested without a DOM. useCaptureWizard wires it to side-effects.

export type CaptureStep = 'capture' | 'prepare' | 'computing' | 'review';

export interface CaptureWizardState {
  step: CaptureStep;
  recording: boolean;
  // ADR-0019: re-recording an already-computed result asks for confirmation first.
  overwriteOpen: boolean;
  // Surfaced start/compute/validate error, or null.
  message: string | null;
}

export type CaptureWizardEvent =
  | { type: 'START' } // recording began
  | { type: 'START_FAILED'; message: string }
  | { type: 'STOP' } // recording stopped (still on capture until the preview is ready)
  | { type: 'PREVIEW_READY' } // transcode done -> Prepare
  | { type: 'COMPUTE' } // solve started
  | { type: 'COMPUTE_OK' } // solve succeeded -> Review
  | { type: 'COMPUTE_FAILED'; message: string } // solve failed -> back to Prepare
  | { type: 'RERECORD' } // always confirms — only reachable from Review (result exists)
  | { type: 'CONFIRM_RERECORD' }
  | { type: 'CANCEL_OVERWRITE' }
  | { type: 'RESET'; step: CaptureStep } // e.g. intrinsic camera switch
  | { type: 'SET_MESSAGE'; message: string | null };

export const initialCaptureWizardState = (step: CaptureStep): CaptureWizardState => ({
  step,
  recording: false,
  overwriteOpen: false,
  message: null,
});

export function captureWizardReducer(
  state: CaptureWizardState,
  event: CaptureWizardEvent,
): CaptureWizardState {
  switch (event.type) {
    case 'START':
      return { ...state, step: 'capture', recording: true, message: null };
    case 'START_FAILED':
      return { ...state, recording: false, message: event.message };
    case 'STOP':
      return { ...state, recording: false };
    case 'PREVIEW_READY':
      return { ...state, step: 'prepare' };
    case 'COMPUTE':
      return { ...state, step: 'computing', message: null };
    case 'COMPUTE_OK':
      return { ...state, step: 'review' };
    case 'COMPUTE_FAILED':
      return { ...state, step: 'prepare', message: event.message };
    case 'RERECORD':
      // ADR-0019: re-record is only reachable from Review, where a result always exists
      // (fresh compute OR a reopened/already-calibrated camera — matrix present but the
      // status may be extrinsic_done). So it ALWAYS asks for overwrite confirmation.
      return { ...state, overwriteOpen: true };
    case 'CONFIRM_RERECORD':
      return { ...state, overwriteOpen: false, recording: false, step: 'capture' };
    case 'CANCEL_OVERWRITE':
      return { ...state, overwriteOpen: false };
    case 'RESET':
      return { ...state, step: event.step, recording: false };
    case 'SET_MESSAGE':
      return { ...state, message: event.message };
  }
}
