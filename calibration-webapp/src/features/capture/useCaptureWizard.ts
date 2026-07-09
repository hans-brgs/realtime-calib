import { useReducer } from 'react';

import {
  captureWizardReducer,
  type CaptureStep,
  initialCaptureWizardState,
} from '@/features/capture/captureWizard';

interface UseCaptureWizardOptions {
  initialStep: CaptureStep;
  // Backend "start recording" (+ any screen side effect); the hook flips recording on success.
  start: () => Promise<void>;
  // Backend "stop recording".
  stop: () => Promise<void>;
  // Runs after Stop — usually the preview transcode's run() (which calls toPrepare on ready).
  afterStop: () => Promise<void>;
  // Dispatch the compute; the hook drives computing -> review, or -> prepare on failure.
  compute: () => Promise<void>;
}

export interface CaptureWizard {
  step: CaptureStep;
  recording: boolean;
  overwriteOpen: boolean;
  message: string | null;
  setMessage: (message: string | null) => void;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<void>;
  runCompute: () => Promise<void>;
  reRecord: () => void;
  confirmReRecord: () => void;
  cancelOverwrite: () => void;
  // Enter Prepare once the preview transcode is ready (called from the transcode onReady).
  toPrepare: () => void;
  // Jump to a step and stop recording (e.g. the intrinsic camera switch).
  reset: (step: CaptureStep) => void;
}

// Drives the shared capture sub-wizard (captureWizard reducer) and wires each transition
// to its screen-provided side-effect. The screen keeps step-dependent effects
// (setActiveIntrinsic / setCaptureView, ADR-0021) by reading the returned `step`.
export function useCaptureWizard({
  initialStep,
  start,
  stop,
  afterStop,
  compute,
}: UseCaptureWizardOptions): CaptureWizard {
  const [state, dispatch] = useReducer(
    captureWizardReducer,
    initialStep,
    initialCaptureWizardState,
  );

  const startRecording = async () => {
    dispatch({ type: 'SET_MESSAGE', message: null });
    try {
      await start();
      dispatch({ type: 'START' });
    } catch (err) {
      dispatch({ type: 'START_FAILED', message: err instanceof Error ? err.message : 'start failed' });
    }
  };

  const stopRecording = async () => {
    await stop();
    dispatch({ type: 'STOP' });
    await afterStop();
  };

  const runCompute = async () => {
    dispatch({ type: 'COMPUTE' });
    try {
      await compute();
      dispatch({ type: 'COMPUTE_OK' });
    } catch (err) {
      dispatch({ type: 'COMPUTE_FAILED', message: err instanceof Error ? err.message : 'compute failed' });
    }
  };

  return {
    step: state.step,
    recording: state.recording,
    overwriteOpen: state.overwriteOpen,
    message: state.message,
    setMessage: (message) => dispatch({ type: 'SET_MESSAGE', message }),
    startRecording,
    stopRecording,
    runCompute,
    reRecord: () => dispatch({ type: 'RERECORD' }),
    confirmReRecord: () => dispatch({ type: 'CONFIRM_RERECORD' }),
    cancelOverwrite: () => dispatch({ type: 'CANCEL_OVERWRITE' }),
    toPrepare: () => dispatch({ type: 'PREVIEW_READY' }),
    reset: (step) => dispatch({ type: 'RESET', step }),
  };
}
