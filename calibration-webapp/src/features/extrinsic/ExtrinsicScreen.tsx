import { LiveKitRoom, useDataChannel } from '@livekit/components-react';
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
  IconPlayerPauseFilled,
  IconPlayerPlayFilled,
  IconPlayerRecordFilled,
  IconPlayerStopFilled,
} from '@tabler/icons-react';
import { lazy, Suspense, useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { ScreenHeader } from '@/components/ScreenHeader';
import { CovisibilityMatrix } from '@/features/extrinsic/CovisibilityMatrix';
import { CameraGrid } from '@/features/preview/PreviewGrid';
import { computeExtrinsicThunk, selectSession } from '@/features/session/sessionSlice';
import {
  type CoverageMetrics,
  type Covisibility,
  coverageReceived,
  covisibilityCleared,
  covisibilityReceived,
  selectCovisibility,
} from '@/features/telemetry/telemetrySlice';
import {
  type ExtrinsicGroup,
  type ExtrinsicResultPayload,
  extrinsicFrameUrl,
  fetchExtrinsicGroups,
  fetchExtrinsicResult,
  fetchToken,
  setCaptureView,
  startExtrinsic,
  stopExtrinsic,
} from '@/transport/httpClient';

type Step = 'capture' | 'prepare' | 'computing' | 'result';

// Lazy so three.js only ships when the operator reaches the 3D array review.
const ArrayReview = lazy(() =>
  import('@/features/review3d/ArrayReview').then((m) => ({ default: m.ArrayReview })),
);

const STEPS: { key: Step; label: string }[] = [
  { key: 'capture', label: 'Capture' },
  { key: 'prepare', label: 'Prepare' },
  { key: 'computing', label: 'Computing' },
  { key: 'result', label: 'Result' },
];

const PLAY_FPS = 8;

function StepIndicator({ step }: { step: Step }) {
  const idx = STEPS.findIndex((s) => s.key === step);
  return (
    <Group gap={8} mb="md">
      {STEPS.map((s, i) => {
        const active = i === idx;
        const done = i < idx;
        return (
          <Group
            key={s.key}
            gap={7}
            px={12}
            py={6}
            style={{
              borderRadius: 'var(--mantine-radius-sm)',
              background: active ? 'rgba(167,139,250,0.14)' : 'var(--rc-panel)',
              border: `1px solid ${active ? 'var(--rc-accent)' : 'var(--rc-border)'}`,
            }}
          >
            <Text
              fz="0.7rem"
              fw={700}
              className="rc-tnum"
              c={active ? 'var(--rc-accent-bright)' : done ? 'var(--rc-success)' : 'dark.3'}
            >
              {done ? '✓' : i + 1}
            </Text>
            <Text fz="0.78rem" c={active ? undefined : 'dark.2'}>
              {s.label}
            </Text>
          </Group>
        );
      })}
    </Group>
  );
}

// Routes both telemetry payload types from the shared data channel to the store.
function TelemetryListener() {
  const dispatch = useAppDispatch();
  useDataChannel('telemetry', (msg) => {
    try {
      const data = JSON.parse(new TextDecoder().decode(msg.payload)) as
        | CoverageMetrics
        | Covisibility;
      if (data?.type === 'coverage_metrics') {
        dispatch(coverageReceived(data));
      } else if (data?.type === 'covisibility') {
        dispatch(covisibilityReceived(data));
      }
    } catch {
      /* ignore malformed telemetry */
    }
  });
  return null;
}

// Prepare replay: scrub the SYNCHRONIZED groups — every camera's frame of the same
// instant side by side (what the compute consumes, spread shown per group).
function GroupScrubber({
  cameras,
  groups,
  index,
  onIndex,
}: {
  cameras: string[];
  groups: ExtrinsicGroup[];
  index: number;
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
                <img
                  src={extrinsicFrameUrl(camera, frame)}
                  alt={`${camera} frame ${frame}`}
                  style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
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
        <Text className="rc-tnum" fz="0.72rem" c="dark.2" w={150} ta="right" style={{ flex: 'none' }}>
          group {Math.min(index, max)} / {max} · {current.spread_ms.toFixed(1)} ms
        </Text>
      </Group>
    </Box>
  );
}

function ResultSummary({ result }: { result: ExtrinsicResultPayload }) {
  return (
    <>
      <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="md" style={{ letterSpacing: '0.07em' }}>
        Array result
      </Text>
      <Text fz="0.72rem" c="dark.2">
        Reprojection error (all cameras)
      </Text>
      <Text ff="heading" fw={700} fz="1.9rem" className="rc-tnum" mb="md">
        {result.error.toFixed(3)}
        <Text span fz="0.9rem" c="dark.2" inherit>
          {' '}
          px
        </Text>
      </Text>
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
  const covisibility = useAppSelector(selectCovisibility);
  const cameras = session?.cameras ?? [];
  const cameraNames = cameras.map((c) => c.name);
  const allDone = cameras.length > 0 && cameras.every((c) => c.status === 'extrinsic_done');
  const anchor = cameras.find((c) => c.index === 0)?.name ?? cameraNames[0] ?? '—';

  const [step, setStep] = useState<Step>(allDone ? 'result' : 'capture');
  const [recording, setRecording] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [overwriteOpen, setOverwriteOpen] = useState(false);

  // Prepare state: synchronized groups + the compute knobs (ADR-0023).
  const [groups, setGroups] = useState<ExtrinsicGroup[]>([]);
  const [groupIndex, setGroupIndex] = useState(0);
  const [stride, setStride] = useState(1);
  const [minShared, setMinShared] = useState(5);
  const [maxSpreadMs, setMaxSpreadMs] = useState<number | ''>('');
  const [result, setResult] = useState<ExtrinsicResultPayload | null>(null);

  // Live cameras are only needed while capturing; Prepare/Result scrub the files.
  // Reporting a non-live view id releases every camera (ADR-0021 view mapping).
  useEffect(() => {
    setCaptureView(step === 'capture' ? 'extrinsic' : 'extrinsic-idle').catch(() => {});
  }, [step]);

  // Refetch the group list when the spread threshold changes (server-side filter,
  // same semantics as the compute). Stride/min-shared are compute-only knobs.
  useEffect(() => {
    if (step !== 'prepare') return;
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
  }, [step, maxSpreadMs]);

  // On the Result step, load the persisted solve (also restores after a reload).
  useEffect(() => {
    if (step !== 'result') return;
    let cancelled = false;
    fetchExtrinsicResult()
      .then((payload) => !cancelled && setResult(payload))
      .catch(() => !cancelled && setResult(null));
    return () => {
      cancelled = true;
    };
  }, [step]);

  const startSweep = async () => {
    setMessage(null);
    try {
      await startExtrinsic();
      dispatch(covisibilityCleared());
      setRecording(true);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'start failed');
    }
  };

  const stopSweep = async () => {
    await stopExtrinsic().catch(() => {});
    setRecording(false);
    setStep('prepare');
  };

  const runCompute = async () => {
    setStep('computing');
    setMessage(null);
    try {
      await dispatch(
        computeExtrinsicThunk({
          stride: stride > 1 ? stride : undefined,
          max_spread_ms: maxSpreadMs === '' ? undefined : maxSpreadMs,
          min_shared: minShared,
        }),
      ).unwrap();
      setStep('result');
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'compute failed');
      setStep('prepare');
    }
  };

  const reRecord = () => {
    if (allDone) {
      setOverwriteOpen(true);
    } else {
      setStep('capture');
    }
  };

  const confirmReRecord = () => {
    setOverwriteOpen(false);
    setRecording(false);
    setStep('capture');
  };

  const scrubbing = step === 'prepare' || step === 'computing';

  return (
    <>
      <TelemetryListener />
      <StepIndicator step={step} />

      <Box
        className="rc-camsetup-grid"
        style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 22 }}
      >
        <Box style={{ minWidth: 0, minHeight: 0, position: 'relative' }}>
          {step === 'result' ? (
            // The Result sub-step IS the 3D array review (spec 3d-extrinsic-review):
            // labeled frustums + corner cloud + board triad + convention selector.
            result ? (
              <Suspense
                fallback={
                  <Center h="100%">
                    <Loader size="sm" />
                  </Center>
                }
              >
                <ArrayReview result={result} onResult={setResult} />
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
              onIndex={setGroupIndex}
            />
          ) : (
            <>
              <CameraGrid />
              {recording && (
                <Group
                  gap={6}
                  px={10}
                  py={5}
                  style={{ position: 'absolute', top: 12, left: 12, borderRadius: 20, background: 'rgba(9,9,11,0.7)' }}
                >
                  <IconPlayerRecordFilled size={13} color="var(--rc-error)" />
                  <Text fz="0.72rem" fw={600}>
                    REC · all cameras
                  </Text>
                </Group>
              )}
            </>
          )}
        </Box>

        <Box
          style={{
            minHeight: 0,
            overflowY: 'auto',
            border: '1px solid var(--rc-border)',
            borderRadius: 'var(--mantine-radius-lg)',
            background: 'var(--rc-panel)',
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          {step === 'result' && result ? (
            <ResultSummary result={result} />
          ) : scrubbing ? (
            <>
              <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="md" style={{ letterSpacing: '0.07em' }}>
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
                min={1}
                max={100}
                mb="md"
              />
              <NumberInput
                label="Sampling stride (1 group / N)"
                value={stride}
                onChange={(v) => setStride(Math.max(1, Number(v) || 1))}
                min={1}
                max={20}
                mb="md"
              />
              <NumberInput
                label="Min shared views per pair"
                value={minShared}
                onChange={(v) => setMinShared(Math.max(2, Number(v) || 2))}
                min={2}
                max={30}
                mb="md"
              />
              <Text fz="0.66rem" c="dark.3">
                {groups.length} synchronized groups under the current threshold.
              </Text>
            </>
          ) : (
            <>
              <Group justify="space-between" mb="md">
                <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" style={{ letterSpacing: '0.07em' }}>
                  Co-visibility
                </Text>
                <Text fz="0.72rem" c="dark.2">
                  anchor {anchor}
                </Text>
              </Group>
              <CovisibilityMatrix data={covisibility} />
              <Text fz="0.66rem" c="dark.3" mt="md">
                Move the board through the shared volume so every pair accumulates
                joint views — especially pairs involving the anchor.
              </Text>
            </>
          )}

          {message && (
            <Text fz="0.72rem" c="var(--rc-error)" mt="sm">
              {message}
            </Text>
          )}

          <Box mt="auto" pt="md">
            {step === 'result' ? (
              <Button
                fullWidth
                variant="light"
                color="gray"
                leftSection={<IconPlayerRecordFilled size={15} />}
                onClick={reRecord}
              >
                Re-record sweep
              </Button>
            ) : scrubbing ? (
              <Button
                fullWidth
                loading={step === 'computing'}
                disabled={groups.length === 0}
                onClick={() => void runCompute()}
              >
                Compute
              </Button>
            ) : recording ? (
              <Button
                fullWidth
                color="red"
                leftSection={<IconPlayerStopFilled size={16} />}
                onClick={() => void stopSweep()}
              >
                Stop
              </Button>
            ) : (
              <Button
                fullWidth
                leftSection={<IconPlayerRecordFilled size={16} />}
                onClick={() => void startSweep()}
              >
                Start synchronized sweep
              </Button>
            )}
          </Box>
        </Box>
      </Box>

      {/* Blocking compute modal (capture released during the solve, ADR-0021/0023). */}
      <Modal
        opened={step === 'computing'}
        onClose={() => {}}
        withCloseButton={false}
        closeOnClickOutside={false}
        closeOnEscape={false}
        centered
        title="Computing camera array"
      >
        <Text fz="0.8rem" c="dark.2" mb="md">
          Pairwise stereo → chaining from {anchor} → bundle adjustment
        </Text>
        <Group justify="center">
          <Loader size="sm" />
        </Group>
      </Modal>

      {/* Overwrite double-validation: re-recording replaces the sweep + the solve. */}
      <Modal opened={overwriteOpen} onClose={() => setOverwriteOpen(false)} centered title="Overwrite array calibration?">
        <Group gap={10} mb="md" wrap="nowrap" align="flex-start">
          <IconAlertTriangle size={20} color="var(--rc-error)" style={{ flex: 'none', marginTop: 2 }} />
          <Text fz="0.84rem" c="dark.1">
            Re-recording will replace the synchronized sweep and the solved array
            (error {result?.error.toFixed(3) ?? '—'} px). The existing result is
            discarded and cannot be recovered.
          </Text>
        </Group>
        <Group justify="flex-end">
          <Button variant="default" onClick={() => setOverwriteOpen(false)}>
            Cancel
          </Button>
          <Button color="red" onClick={confirmReRecord}>
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
export function ExtrinsicScreen() {
  const [connection, setConnection] = useState<{ serverUrl: string; token: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchToken()
      .then((response) => {
        if (!cancelled) {
          setConnection({ serverUrl: import.meta.env.VITE_LIVEKIT_URL, token: response.token });
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Box p={{ base: 'md', sm: 'xl' }} h="100%" style={{ display: 'flex', flexDirection: 'column' }}>
      <ScreenHeader
        title="Extrinsics"
        subtitle="One synchronized sweep for the whole rig: capture with live co-visibility, prepare, compute, review the array."
      />
      {error ? (
        <Center style={{ flex: 1 }}>
          <Text c="red">{error}</Text>
        </Center>
      ) : !connection ? (
        <Center style={{ flex: 1 }}>
          <Loader />
        </Center>
      ) : (
        <LiveKitRoom
          serverUrl={connection.serverUrl}
          token={connection.token}
          connect
          audio={false}
          video={false}
          style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
        >
          <ExtrinsicInner />
        </LiveKitRoom>
      )}
    </Box>
  );
}
