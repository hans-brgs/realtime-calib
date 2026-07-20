import {
  ActionIcon,
  Box,
  Button,
  Center,
  Group,
  Loader,
  Modal,
  NumberInput,
  Slider,
  Text,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconCheck,
  IconPlayerPauseFilled,
  IconPlayerPlayFilled,
  IconPlayerRecordFilled,
  IconPlayerStopFilled,
} from '@tabler/icons-react';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { PhaseStepper } from '@/components/PhaseStepper';
import { ScreenHeader } from '@/components/ScreenHeader';
import { CaptureWizardLayout } from '@/features/capture/CaptureWizardLayout';
import { TranscodePreparingModal } from '@/features/capture/TranscodePreparingModal';
import { useCaptureWizard } from '@/features/capture/useCaptureWizard';
import { usePreviewTranscode } from '@/features/capture/usePreviewTranscode';
import { CovisibilityMatrix } from '@/features/extrinsic/CovisibilityMatrix';
import { CameraGrid } from '@/features/preview/PreviewGrid';
import { selectDefaults } from '@/features/session/defaultsSlice';
import {
  computeExtrinsicThunk,
  selectSession,
  validateExtrinsicThunk,
} from '@/features/session/sessionSlice';
import { covisibilityCleared, selectCovisibility } from '@/features/telemetry/telemetrySlice';
import {
  errorMessage,
  type ExtrinsicGroup,
  type ExtrinsicResultPayload,
  extrinsicPreviewUrl,
  fetchExtrinsicGroups,
  fetchExtrinsicPreviewStatus,
  fetchExtrinsicResult,
  retryExtrinsicPreview,
  setCaptureView,
  startExtrinsic,
  stopExtrinsic,
} from '@/transport/httpClient';

// Lazy so three.js only ships when the operator reaches the 3D array review.
const ArrayReview = lazy(() =>
  import('@/features/review3d/ArrayReview').then((m) => ({ default: m.ArrayReview })),
);

const PHASES = [
  { key: 'capture', label: 'Capture', sub: 'synchronized sweep' },
  { key: 'prepare', label: 'Prepare', sub: 'groups + thresholds' },
  { key: 'computing', label: 'Computing', sub: 'pairs + chain + BA' },
  { key: 'review', label: 'Result', sub: '3D review + orient' },
];

const PLAY_FPS = 8;

// Prepare replay: scrub the SYNCHRONIZED groups — every camera's frame of the same
// instant side by side (what the compute consumes, spread shown per group).
// One camera's frame of a synchronized group, shown by seeking its CFR-retimed
// preview mp4 (ADR-0027/0037): frame i sits exactly at (i + 0.5) / fps, where fps
// is SERVED by the transcode status (the recording's own rate). The lockstep
// across cameras comes from the DATA (per-camera indices of the group, ADR-0007)
// — free-running cameras never share a clock.
function PreviewFrame({
  camera,
  index,
  fps,
  version,
}: {
  camera: string;
  index: number;
  fps: number;
  version: string; // cache-buster served by the transcode status (stale-video guard)
}) {
  const video = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const element = video.current;
    if (element && element.readyState > 0) {
      element.currentTime = (index + 0.5) / fps;
    }
  }, [index, fps]);

  return (
    <video
      ref={video}
      src={version ? `${extrinsicPreviewUrl(camera)}?v=${version}` : extrinsicPreviewUrl(camera)}
      muted
      playsInline
      preload="auto"
      onLoadedMetadata={(event) => {
        event.currentTarget.currentTime = (index + 0.5) / fps;
      }}
      style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
    />
  );
}

function GroupScrubber({
  cameras,
  groups,
  index,
  fps,
  versions,
  onIndex,
}: {
  cameras: string[];
  groups: ExtrinsicGroup[];
  index: number;
  fps: number; // index <-> time rate served by the transcode status (ADR-0037)
  versions: Record<string, string>; // per-camera preview cache-busters (served)
  onIndex: (i: number) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const max = Math.max(0, groups.length - 1);
  const current = groups[Math.min(index, max)];

  useEffect(() => {
    if (!playing || groups.length === 0) return;
    const id = setInterval(() => onIndex(index >= max ? 0 : index + 1), 1000 / PLAY_FPS);
    return () => clearInterval(id);
  }, [playing, index, max, groups.length, onIndex]);

  if (groups.length === 0 || !current) {
    return (
      <Center
        h="100%"
        style={{ border: '1px dashed var(--rc-border)', borderRadius: 'var(--mantine-radius-md)' }}
      >
        <Text c="dark.3" fz="0.84rem" ta="center" maw={300}>
          No synchronized group under the current spread threshold.
        </Text>
      </Center>
    );
  }

  const cols = Math.ceil(Math.sqrt(cameras.length));
  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gap: 8,
        }}
      >
        {cameras.map((camera) => {
          const frame = current.frames[camera];
          return (
            <Box
              key={camera}
              style={{
                position: 'relative',
                background: '#000',
                borderRadius: 'var(--mantine-radius-md)',
                overflow: 'hidden',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                minHeight: 0,
              }}
            >
              {frame !== undefined ? (
                <PreviewFrame
                  camera={camera}
                  index={frame}
                  fps={fps}
                  version={versions[camera] ?? ''}
                />
              ) : (
                <Text fz="0.7rem" c="dark.3">
                  not in this group
                </Text>
              )}
              <Text
                fz="0.62rem"
                px={6}
                py={2}
                style={{
                  position: 'absolute',
                  top: 6,
                  left: 6,
                  borderRadius: 12,
                  background: 'rgba(9,9,11,0.7)',
                }}
              >
                {camera}
              </Text>
            </Box>
          );
        })}
      </Box>
      <Group mt="sm" gap="sm" wrap="nowrap">
        <ActionIcon
          variant="light"
          color="violet"
          size="lg"
          aria-label={playing ? 'Pause' : 'Play'}
          onClick={() => setPlaying((p) => !p)}
        >
          {playing ? <IconPlayerPauseFilled size={16} /> : <IconPlayerPlayFilled size={16} />}
        </ActionIcon>
        <Slider
          flex={1}
          min={0}
          max={max}
          value={Math.min(index, max)}
          onChange={(value) => {
            setPlaying(false);
            onIndex(value);
          }}
          label={null}
          color="violet"
        />
        <Text
          className="rc-tnum"
          fz="0.72rem"
          c="dark.2"
          w={150}
          ta="right"
          style={{ flex: 'none' }}
        >
          group {Math.min(index, max)} / {max} · {current.spread_ms.toFixed(1)} ms
        </Text>
      </Group>
    </Box>
  );
}

function ResultSummary({ result }: { result: ExtrinsicResultPayload }) {
  return (
    <>
      <Text
        fz="0.66rem"
        fw={600}
        c="dark.3"
        tt="uppercase"
        mb="md"
        style={{ letterSpacing: '0.07em' }}
      >
        Array result
      </Text>
      <Text fz="0.72rem" c="dark.2">
        Reprojection error (all cameras)
      </Text>
      <Text ff="heading" fw={700} fz="1.9rem" className="rc-tnum" mb={4}>
        {result.error.toFixed(3)}
        <Text span fz="0.9rem" c="dark.2" inherit>
          {' '}
          px
        </Text>
      </Text>
      {/* The BA stopped on its evaluation ceiling instead of a tolerance
          (ADR-0036): the poses are the best-so-far, not a converged optimum —
          say so rather than let a truncated solve pass for a good one. */}
      {result.ba_converged === false && (
        <Group gap={5} wrap="nowrap" mb="md">
          <IconAlertTriangle size={13} color="var(--rc-warning)" style={{ flex: 'none' }} />
          <Text fz="0.66rem" c="var(--rc-warning)" style={{ lineHeight: 1.4 }}>
            Bundle adjustment hit its iteration ceiling ({result.ba_nfev} evaluations) — these
            poses are the best so far, not a converged optimum.
          </Text>
        </Group>
      )}
      {result.ba_converged !== false && <Box mb="md" />}
      {result.cameras.map((camera) => {
        const deviation = result.per_camera_error[camera];
        // Spec deviation highlight: green <= 0.25 px, amber <= 0.5, red above.
        const color =
          deviation == null
            ? undefined
            : deviation <= 0.25
              ? 'var(--rc-success)'
              : deviation <= 0.5
                ? 'var(--rc-warning)'
                : 'var(--rc-error)';
        return (
          <Group key={camera} justify="space-between" mt="sm">
            <Text fz="0.72rem" c="dark.2">
              {camera} {camera === result.cameras[0] ? '· anchor' : ''}
            </Text>
            <Text fz="0.78rem" fw={600} className="rc-tnum" style={{ color }}>
              {deviation?.toFixed(3) ?? '—'} px
            </Text>
          </Group>
        );
      })}
      <Group justify="space-between" mt="md">
        <Text fz="0.72rem" c="dark.2">
          Groups / 3D points
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {result.group_count} / {result.point_count}
        </Text>
      </Group>
      {Object.entries(result.pair_errors).map(([pair, error]) => (
        <Group key={pair} justify="space-between" mt="sm">
          <Text fz="0.72rem" c="dark.2">
            {pair.replace('|', ' × ')}
          </Text>
          <Text fz="0.78rem" fw={600} className="rc-tnum">
            {error.toFixed(4)}
          </Text>
        </Group>
      ))}
    </>
  );
}

function ExtrinsicInner() {
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  // Imported session (ADR-0031): capture is neutralised — the sub-wizard starts
  // on Prepare (the sweep came with the archive) and never offers recording.
  const imported = session?.mode === 'load-from-files';
  const covisibility = useAppSelector(selectCovisibility);
  const cameras = session?.cameras ?? [];
  const cameraNames = cameras.map((c) => c.name);
  const allDone = cameras.length > 0 && cameras.every((c) => c.status === 'extrinsic_done');
  const anchor = cameras.find((c) => c.index === 0)?.name ?? cameraNames[0] ?? '—';
  // Same fallback as the service's effective_extrinsic_board: the board frame
  // anchor differs per target (marker center vs ChArUco first corner).
  const extrinsicBoard = session?.extrinsic_board ?? session?.intrinsic_board ?? null;
  const markerBoard = extrinsicBoard?.board_type === 'aruco';

  // Backend-served knob defaults/bounds (GET /defaults, ADR-0036).
  const defaults = useAppSelector(selectDefaults);

  // Prepare state: synchronized groups + the compute knobs (ADR-0023/0033/0036).
  // `min_shared` deliberately has no control here since ADR-0036 (API-only knob;
  // possible reintegration later under an Advanced section).
  const [groups, setGroups] = useState<ExtrinsicGroup[]>([]);
  const [groupIndex, setGroupIndex] = useState(0);
  // Index <-> time rate of the preview mp4s, SERVED by the transcode status
  // (dynamic contract, ADR-0037). 30 is a pre-seed placeholder only — always
  // overwritten before Prepare renders.
  const [previewFps, setPreviewFps] = useState(30);
  // Per-camera preview cache-busters (served): a re-record must never scrub
  // browser-cached stale videos.
  const [previewVersions, setPreviewVersions] = useState<Record<string, string>>({});
  const [stride, setStride] = useState(1);
  const [maxGroups, setMaxGroups] = useState(5);
  const [maxSpreadMs, setMaxSpreadMs] = useState<number | ''>('');
  const [result, setResult] = useState<ExtrinsicResultPayload | null>(null);

  // Seed (and RE-seed on a board-type change) the board-type-dependent knobs from
  // the served defaults — a ChArUco board detects ~10x slower than a marker, so
  // both budgets differ per type.
  useEffect(() => {
    if (!defaults) return;
    setStride(markerBoard ? defaults.extrinsic_stride_marker : defaults.extrinsic_stride_charuco);
    setMaxGroups(markerBoard ? defaults.max_groups_marker : defaults.max_groups_charuco);
  }, [defaults, markerBoard]);

  const strideBounds = defaults?.extrinsic_stride_bounds ?? [1, 30];
  const maxGroupsBounds = defaults?.max_groups_bounds ?? [5, 960];
  // "1 group every N" over the spread-filtered candidates: what the compute will
  // actually detect on (the kept count is bounded by it — both shown live).
  const analyzedGroups = groups.length > 0 ? Math.ceil(groups.length / Math.max(1, stride)) : 0;

  // Shared capture sub-wizard (D5): capture -> prepare -> computing -> review.
  const wizard = useCaptureWizard({
    initialStep: allDone ? 'review' : imported ? 'prepare' : 'capture',
    start: async () => {
      await startExtrinsic();
      dispatch(covisibilityCleared());
    },
    stop: async () => {
      await stopExtrinsic().catch(() => {});
    },
    afterStop: () => transcode.run(),
    compute: async () => {
      await dispatch(
        computeExtrinsicThunk({
          stride,
          max_groups: maxGroups,
          max_spread_ms: maxSpreadMs === '' ? undefined : maxSpreadMs,
        }),
      ).unwrap();
    },
  });

  // Background preview transcodes of the sweep (ADR-0027) behind a blocking modal,
  // then enter Prepare. The error can surface on any camera's transcode job.
  const transcode = usePreviewTranscode({
    poll: fetchExtrinsicPreviewStatus,
    onReady: (status) => {
      // All sweep cameras share the configured fps; take the served rate.
      const rates = Object.values(status.cameras)
        .map((c) => c.fps)
        .filter((f) => f > 0);
      if (rates.length > 0) setPreviewFps(Math.max(...rates));
      setPreviewVersions(
        Object.fromEntries(
          Object.entries(status.cameras).map(([name, c]) => [name, c.version]),
        ),
      );
      wizard.toPrepare();
    },
    getError: (status) => Object.values(status.cameras).find((c) => c.error)?.error ?? null,
    retryJob: retryExtrinsicPreview,
  });

  // Live cameras are only needed while capturing; Prepare/Review scrub the files.
  // Reporting a non-live view id releases every camera (ADR-0021 view mapping).
  useEffect(() => {
    setCaptureView(wizard.step === 'capture' ? 'extrinsic' : 'idle').catch(() => {});
  }, [wizard.step]);

  // Imported session landing on Prepare: open the sweep's previews right away
  // (usually already transcoded, kicked at import). An intrinsics-only import
  // resolves to 'missing' -> Prepare with zero groups and Compute disabled.
  useEffect(() => {
    if (imported && !allDone) {
      void transcode.run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Refetch the group list when the spread threshold changes (server-side filter,
  // same semantics as the compute). Stride/max-groups are compute-only knobs.
  useEffect(() => {
    if (wizard.step !== 'prepare') return;
    let cancelled = false;
    fetchExtrinsicGroups(maxSpreadMs === '' ? undefined : { max_spread_ms: maxSpreadMs })
      .then((body) => {
        if (cancelled) return;
        setGroups(body.groups);
        setGroupIndex(0);
      })
      .catch(() => !cancelled && setGroups([]));
    return () => {
      cancelled = true;
    };
  }, [wizard.step, maxSpreadMs]);

  // On the Review step, load the persisted solve (also restores after a reload).
  useEffect(() => {
    if (wizard.step !== 'review') return;
    let cancelled = false;
    fetchExtrinsicResult()
      .then((payload) => !cancelled && setResult(payload))
      .catch(() => !cancelled && setResult(null));
    return () => {
      cancelled = true;
    };
  }, [wizard.step]);

  // Sign-off: the server advances the step to 'export' and the wizard rail
  // follows the persisted step, so no explicit navigation is needed here.
  const validateAndExport = async () => {
    wizard.setMessage(null);
    try {
      await dispatch(validateExtrinsicThunk()).unwrap();
    } catch (err) {
      wizard.setMessage(errorMessage(err, 'validate failed'));
    }
  };

  const scrubbing = wizard.step === 'prepare' || wizard.step === 'computing';

  return (
    <>
      <CaptureWizardLayout
        stepper={
          <PhaseStepper
            // Imported session: the capture phase does not exist (ADR-0031).
            phases={imported ? PHASES.filter((p) => p.key !== 'capture') : PHASES}
            current={wizard.step}
          />
        }
        main={
          wizard.step === 'review' ? (
            // The Review sub-step IS the 3D array review (spec 3d-extrinsic-review):
            // labeled frustums + corner cloud + board triad + convention selector.
            result ? (
              <Suspense
                fallback={
                  <Center h="100%">
                    <Loader size="sm" />
                  </Center>
                }
              >
                <ArrayReview result={result} onResult={setResult} markerBoard={markerBoard} />
              </Suspense>
            ) : (
              <Center h="100%">
                <Text c="dark.3" fz="0.84rem">
                  Loading array…
                </Text>
              </Center>
            )
          ) : scrubbing ? (
            <GroupScrubber
              cameras={cameraNames}
              groups={groups}
              index={groupIndex}
              fps={previewFps}
              versions={previewVersions}
              onIndex={setGroupIndex}
            />
          ) : (
            <>
              <CameraGrid />
              {wizard.recording && (
                <Group
                  gap={6}
                  px={10}
                  py={5}
                  style={{
                    position: 'absolute',
                    top: 12,
                    left: 12,
                    borderRadius: 20,
                    background: 'rgba(9,9,11,0.7)',
                  }}
                >
                  <IconPlayerRecordFilled size={13} color="var(--rc-error)" />
                  <Text fz="0.72rem" fw={600}>
                    REC · all cameras
                  </Text>
                </Group>
              )}
            </>
          )
        }
        panel={
          wizard.step === 'review' && result ? (
            <ResultSummary result={result} />
          ) : scrubbing ? (
            <>
              <Text
                fz="0.66rem"
                fw={600}
                c="dark.3"
                tt="uppercase"
                mb="md"
                style={{ letterSpacing: '0.07em' }}
              >
                Prepare · from recording
              </Text>
              <Group justify="space-between" mb="md">
                <Text fz="0.72rem" c="dark.2">
                  Anchor (world origin)
                </Text>
                <Text fz="0.78rem" fw={600}>
                  {anchor}
                </Text>
              </Group>
              <NumberInput
                label="Max sync spread (ms)"
                description="Groups above this timestamp spread are discarded"
                value={maxSpreadMs}
                onChange={(v) => setMaxSpreadMs(typeof v === 'number' ? v : '')}
                min={defaults?.max_spread_ms_bounds[0] ?? 1}
                max={defaults?.max_spread_ms_bounds[1] ?? 100}
                mb="md"
              />
              <NumberInput
                label="Sampling stride (1 group / N)"
                description={`${analyzedGroups} of ${groups.length} groups will be analyzed`}
                value={stride}
                onChange={(v) => setStride(Math.max(strideBounds[0], Number(v) || strideBounds[0]))}
                min={strideBounds[0]}
                max={strideBounds[1]}
                mb="md"
              />
              <NumberInput
                label="Max groups (best kept)"
                description={`Keeps the ${Math.min(maxGroups, analyzedGroups)} sharpest of the analyzed groups`}
                value={maxGroups}
                onChange={(v) =>
                  setMaxGroups(Math.max(maxGroupsBounds[0], Number(v) || maxGroupsBounds[0]))
                }
                min={maxGroupsBounds[0]}
                max={maxGroupsBounds[1]}
                mb="md"
              />
              <Text fz="0.66rem" c="dark.3">
                {groups.length} synchronized groups under the current threshold.
              </Text>
            </>
          ) : (
            <>
              <Group justify="space-between" mb="md">
                <Text
                  fz="0.66rem"
                  fw={600}
                  c="dark.3"
                  tt="uppercase"
                  style={{ letterSpacing: '0.07em' }}
                >
                  Co-visibility
                </Text>
                <Text fz="0.72rem" c="dark.2">
                  anchor {anchor}
                </Text>
              </Group>
              <CovisibilityMatrix data={covisibility} />
              <Text fz="0.66rem" c="dark.3" mt="md">
                Move the board through the shared volume so every pair accumulates joint views —
                especially pairs involving the anchor.
              </Text>
            </>
          )
        }
        action={
          wizard.step === 'review' ? (
            <>
              {/* Sign-off: the persisted step moves to 'export' and the wizard
                  rail follows — this button IS the navigation to Export. */}
              <Button
                fullWidth
                mb="sm"
                leftSection={<IconCheck size={15} />}
                onClick={() => void validateAndExport()}
              >
                Validate → Export
              </Button>
              {/* Back to Prepare on the SAME sweep: retune stride/spread/min-shared,
                  then Compute again (the preview mp4s are already there). */}
              <Button fullWidth variant="light" mb="sm" onClick={() => void transcode.run()}>
                Recompute (tune again)
              </Button>
              {/* No re-record on an imported session (ADR-0031): there are no live
                  cameras, and a sweep would overwrite the imported videos. */}
              {!imported && (
                <Button
                  fullWidth
                  variant="light"
                  color="gray"
                  leftSection={<IconPlayerRecordFilled size={15} />}
                  onClick={wizard.reRecord}
                >
                  Re-record sweep
                </Button>
              )}
            </>
          ) : scrubbing ? (
            <Button
              fullWidth
              loading={wizard.step === 'computing'}
              disabled={groups.length === 0}
              onClick={() => void wizard.runCompute()}
            >
              Compute
            </Button>
          ) : wizard.recording ? (
            <Button
              fullWidth
              color="red"
              leftSection={<IconPlayerStopFilled size={16} />}
              onClick={() => void wizard.stopRecording()}
            >
              Stop
            </Button>
          ) : (
            <Button
              fullWidth
              leftSection={<IconPlayerRecordFilled size={16} />}
              onClick={() => void wizard.startRecording()}
            >
              Start synchronized sweep
            </Button>
          )
        }
        message={wizard.message}
      />

      {/* Blocking compute modal (capture released during the solve, ADR-0021/0023). */}
      <Modal
        opened={wizard.step === 'computing'}
        onClose={() => {}}
        withCloseButton={false}
        closeOnClickOutside={false}
        closeOnEscape={false}
        centered
        title="Computing camera array"
      >
        <Text fz="0.8rem" c="dark.2">
          Pairwise stereo → chaining from {anchor} → bundle adjustment
        </Text>
        <Text fz="0.72rem" c="dark.3" mt={6} mb="md">
          Board detection scans the recorded sweep first — this can take a while (tens of seconds on
          long recordings).
        </Text>
        <Group justify="center">
          <Loader size="sm" />
        </Group>
      </Modal>

      {/* Preview transcodes (ADR-0027): block the Stop -> Prepare transition. */}
      <TranscodePreparingModal
        status={transcode.status}
        onRetry={() => void transcode.retry()}
        onClose={transcode.dismiss}
        label="Transcoding previews…"
      />

      {/* Overwrite double-validation: re-recording replaces the sweep + the solve. */}
      <Modal
        opened={wizard.overwriteOpen}
        onClose={wizard.cancelOverwrite}
        centered
        title="Overwrite array calibration?"
      >
        <Group gap={10} mb="md" wrap="nowrap" align="flex-start">
          <IconAlertTriangle
            size={20}
            color="var(--rc-error)"
            style={{ flex: 'none', marginTop: 2 }}
          />
          <Text fz="0.84rem" c="dark.1">
            Re-recording will replace the synchronized sweep and the solved array (error{' '}
            {result?.error.toFixed(3) ?? '—'} px). The existing result is discarded and cannot be
            recovered.
          </Text>
        </Group>
        <Group justify="flex-end">
          <Button variant="default" onClick={wizard.cancelOverwrite}>
            Cancel
          </Button>
          <Button color="red" onClick={wizard.confirmReRecord}>
            Discard &amp; re-record
          </Button>
        </Group>
      </Modal>
    </>
  );
}

// Extrinsic array calibration (ADR-0023, spec extrinsic-calibration-flow): ONE pass
// for the whole rig — synchronized sweep (co-visibility live) → prepare (group
// scrubber + knobs) → compute (stereo init + chaining + BA) → result (3D review).
// The LiveKit room lives at the App level (RoomProvider) — this screen consumes it.
export function ExtrinsicScreen() {
  return (
    <Box p={{ base: 'md', sm: 'xl' }} h="100%" style={{ display: 'flex', flexDirection: 'column' }}>
      <ScreenHeader
        title="Extrinsics"
        subtitle="One synchronized sweep for the whole rig: capture with live co-visibility, prepare, compute, review the array."
      />
      <Box style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <ExtrinsicInner />
      </Box>
    </Box>
  );
}
