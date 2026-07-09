import { useState } from 'react';

// Shared "wait for the background H.264 preview transcode" flow (ADR-0027), used by the
// intrinsic and extrinsic Stop -> Prepare transitions. The service transcodes each
// recording once in the background; the UI polls the job status behind a blocking modal
// until it is ready. Only the poll endpoint, the ready action and the error shape differ
// between the two screens — the poll loop and the modal state live here once.

// Poll cadence: retry every 700 ms, up to ~105 s — matches the previous per-screen loops.
const POLL_INTERVAL_MS = 700;
const MAX_POLLS = 150;

type PreviewState = 'missing' | 'running' | 'done' | 'failed';

interface UsePreviewTranscodeOptions<S extends { state: PreviewState }> {
  // Fetch the current transcode job status (per-screen endpoint).
  poll: () => Promise<S>;
  // Called once the transcode is ready ('done' or 'missing') — the screen enters Prepare.
  onReady: (status: S) => void;
  // Extract a human error from a 'failed'/timed-out status (shape differs per screen).
  getError?: (status: S) => string | null;
  // Re-trigger the transcode job (per-screen endpoint) before polling again.
  retryJob?: () => Promise<unknown>;
}

export interface PreviewTranscode {
  // null = idle (modal closed), 'running' = spinner, any other string = error message.
  status: string | null;
  run: () => Promise<void>;
  retry: () => Promise<void>;
  dismiss: () => void;
}

export function usePreviewTranscode<S extends { state: PreviewState }>({
  poll,
  onReady,
  getError,
  retryJob,
}: UsePreviewTranscodeOptions<S>): PreviewTranscode {
  const [status, setStatus] = useState<string | null>(null);

  const run = async () => {
    setStatus('running');
    try {
      let current = await poll();
      for (let i = 0; current.state === 'running' && i < MAX_POLLS; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
        current = await poll();
      }
      if (current.state === 'failed' || current.state === 'running') {
        setStatus(getError?.(current) ?? 'preview transcode timed out');
        return;
      }
      setStatus(null);
      onReady(current);
    } catch (cause) {
      setStatus(cause instanceof Error ? cause.message : 'preview transcode failed');
    }
  };

  const retry = async () => {
    await retryJob?.().catch(() => {});
    await run();
  };

  return { status, run, retry, dismiss: () => setStatus(null) };
}
