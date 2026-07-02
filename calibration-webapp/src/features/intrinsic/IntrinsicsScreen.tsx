import {
  isTrackReference,
  LiveKitRoom,
  useDataChannel,
  useTracks,
} from '@livekit/components-react';
import {
  Box,
  Button,
  Center,
  Group,
  Loader,
  Modal,
  NumberInput,
  Progress,
  SegmentedControl,
  Text,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconPlayerRecordFilled,
  IconPlayerStopFilled,
} from '@tabler/icons-react';
import { Track } from 'livekit-client';
import { lazy, Suspense, useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { ScreenHeader } from '@/components/ScreenHeader';
import { CoverageHeatmap } from '@/features/intrinsic/CoverageHeatmap';
import { PrepareScrubber } from '@/features/intrinsic/PrepareScrubber';
import { CameraTile } from '@/features/preview/CameraTile';
import { computeIntrinsicThunk, selectSession } from '@/features/session/sessionSlice';
import {
  type CoverageMetrics,
  coverageReceived,
  selectCoverage,
} from '@/features/telemetry/telemetrySlice';
import {
  fetchIntrinsicFrameCount,
  fetchIntrinsicMetrics,
  fetchToken,
  type IntrinsicMetrics,
  setActiveIntrinsic,
  startIntrinsic,
  stopIntrinsic,
} from '@/transport/httpClient';
import type { CameraConfig } from '@/transport/types';

type ResultsView = 'coverage' | 'poses';

// Lazy so three.js / R3F only load when the operator opens the 3D pose view.
const PoseScene = lazy(() =>
  import('@/features/intrinsic/PoseScene').then((m) => ({ default: m.PoseScene })),
);

type Step = 'capture' | 'prepare' | 'computing' | 'results';

// Board coverage (extrapolated area / frame) → colour band; green >= 0.50 = calib.io.
function fillColor(coverage: number): string {
  if (coverage < 0.15) return 'var(--rc-error)';
  if (coverage < 0.3) return '#fb923c';
  if (coverage < 0.5) return 'var(--rc-warning)';
  return 'var(--rc-success)';
}

function Gauge({ label, value, pct, color }: { label: string; value: string; pct: number; color: string }) {
  return (
    <Box mb="md">
      <Group justify="space-between" mb={5}>
        <Text fz="0.72rem" c="dark.2">
          {label}
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum" style={{ color }}>
          {value}
        </Text>
      </Group>
      <Box style={{ height: 8, borderRadius: 6, background: 'var(--rc-input)', overflow: 'hidden' }}>
        <Box style={{ width: `${Math.round(pct * 100)}%`, height: '100%', background: color }} />
      </Box>
    </Box>
  );
}

function GaugesPanel({ coverage }: { coverage: CoverageMetrics | null }) {
  const found = coverage?.board_found ?? false;
  const fill = coverage?.board_coverage ?? 0;
  return (
    <>
      <Group justify="space-between" mb="md">
        <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" style={{ letterSpacing: '0.07em' }}>
          Live gauges
        </Text>
        <Text
          fz="0.62rem"
          px={8}
          py={2}
          style={{ borderRadius: 20, background: 'rgba(251,191,36,0.14)', color: 'var(--rc-warning)' }}
        >
          indicative
        </Text>
      </Group>

      <Gauge
        label="Board coverage (≥ 50 % target)"
        value={`${Math.round(fill * 100)}%`}
        pct={fill}
        color={fillColor(fill)}
      />

      <Group justify="space-between" mt="lg">
        <Text fz="0.72rem" c="dark.2">
          Sharpness gate
        </Text>
        <Text fz="0.76rem" style={{ color: coverage?.sharpness_ok ? 'var(--rc-success)' : 'var(--rc-error)' }}>
          {coverage?.sharpness_ok ? 'sharp' : 'too blurry'}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Tilt vs frontal
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {coverage?.tilt_deg != null ? `${Math.round(coverage.tilt_deg)}°` : '—'}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Corners detected
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {coverage?.grid_count ?? 0}
        </Text>
      </Group>
      {!found && (
        <Text fz="0.72rem" c="dark.3" mt="md">
          No board detected — bring the printed board into view.
        </Text>
      )}
    </>
  );
}

function ResultPanel({ camera, metrics }: { camera: CameraConfig; metrics: IntrinsicMetrics | null }) {
  const coveragePct = metrics ? Math.round(metrics.image_coverage * 100) : null;
  const bins = metrics?.orientation_bins ?? null;
  return (
    <>
      <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="md" style={{ letterSpacing: '0.07em' }}>
        Result
      </Text>
      <Text fz="0.72rem" c="dark.2">
        Reprojection error
      </Text>
      <Text ff="heading" fw={700} fz="1.9rem" className="rc-tnum" mb="md">
        {camera.calibration_error?.toFixed(3) ?? '—'}
        <Text span fz="0.9rem" c="dark.2" inherit>
          {' '}
          px
        </Text>
      </Text>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Image coverage (≥ 80 % target)
        </Text>
        <Text
          fz="0.78rem"
          fw={600}
          className="rc-tnum"
          c={coveragePct == null ? undefined : coveragePct >= 80 ? 'var(--rc-success)' : 'var(--rc-warning)'}
        >
          {coveragePct == null ? '—' : `${coveragePct}%`}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Board orientations (≥ 4 / 8)
        </Text>
        <Text
          fz="0.78rem"
          fw={600}
          className="rc-tnum"
          c={bins == null ? undefined : bins >= 4 ? 'var(--rc-success)' : 'var(--rc-warning)'}
        >
          {bins == null ? '—' : `${bins}/8`}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Corners used
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {camera.grid_count ?? '—'}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Focal (fx, fy)
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {camera.matrix ? `${camera.matrix[0][0].toFixed(1)}, ${camera.matrix[1][1].toFixed(1)}` : '—'}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Principal point (cx, cy)
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {camera.matrix ? `${camera.matrix[0][2].toFixed(1)}, ${camera.matrix[1][2].toFixed(1)}` : '—'}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Distortion (k1, k2)
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {camera.distortions ? `${camera.distortions[0].toFixed(3)}, ${camera.distortions[1].toFixed(3)}` : '—'}
        </Text>
      </Group>
      <Text fz="0.66rem" c="dark.3" mt="md">
        Calibrated at {camera.width * camera.resize_factor}×{camera.height * camera.resize_factor}
        {camera.resize_factor !== 1 ? ` (native ${camera.width}×${camera.height}, ×${camera.resize_factor})` : ''}
      </Text>
    </>
  );
}

interface PreparePanelProps {
  frame: number;
  trimStart: number;
  trimEnd: number;
  stride: number;
  keyframeCap: number;
  onTrimStart: (n: number) => void;
  onTrimEnd: (n: number) => void;
  onStride: (n: number) => void;
  onCap: (n: number) => void;
}

// Right-side dashboard in the Prepare step (ADR-0022): trim + sampling stride +
// keyframe cap, forwarded to the compute. Components mirror design/realtime-calib.dc.html.
function PreparePanel({
  frame,
  trimStart,
  trimEnd,
  stride,
  keyframeCap,
  onTrimStart,
  onTrimEnd,
  onStride,
  onCap,
}: PreparePanelProps) {
  return (
    <>
      <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="md" style={{ letterSpacing: '0.07em' }}>
        Prepare · from recording
      </Text>

      <Group justify="space-between" mb={6}>
        <Text fz="0.72rem" c="dark.2">
          Trim (frames)
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {trimStart}–{trimEnd}
        </Text>
      </Group>
      <Group gap="xs" mb="lg" grow>
        <Button size="xs" variant="light" color="gray" onClick={() => onTrimStart(frame)}>
          Set in @ {frame}
        </Button>
        <Button size="xs" variant="light" color="gray" onClick={() => onTrimEnd(frame)}>
          Set out @ {frame}
        </Button>
      </Group>

      <NumberInput
        label="Sampling stride (1 frame / N)"
        value={stride}
        onChange={(v) => onStride(Math.max(1, Number(v) || 1))}
        min={1}
        max={30}
        mb="md"
      />
      <NumberInput
        label="Keyframe cap (max kept)"
        value={keyframeCap}
        onChange={(v) => onCap(Math.max(6, Number(v) || 6))}
        min={6}
        max={60}
        mb="md"
      />
      <Text fz="0.66rem" c="dark.3">
        Fewer keyframes → faster compute, potentially lower coverage.
      </Text>
    </>
  );
}

const STEPS: { key: Step; label: string }[] = [
  { key: 'capture', label: 'Capture' },
  { key: 'prepare', label: 'Prepare' },
  { key: 'computing', label: 'Computing' },
  { key: 'results', label: 'Results' },
];

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

function TelemetryListener() {
  const dispatch = useAppDispatch();
  useDataChannel('telemetry', (msg) => {
    try {
      const data = JSON.parse(new TextDecoder().decode(msg.payload)) as CoverageMetrics;
      if (data?.type === 'coverage_metrics') {
        dispatch(coverageReceived(data));
      }
    } catch {
      /* ignore malformed telemetry */
    }
  });
  return null;
}

function IntrinsicsInner() {
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  const cameras = session?.cameras ?? [];
  const [active, setActive] = useState<string | null>(cameras[0]?.name ?? null);
  const coverage = useAppSelector(selectCoverage(active));
  const camera = cameras.find((c) => c.name === active) ?? null;

  const [step, setStep] = useState<Step>('capture');
  const [recording, setRecording] = useState(false);
  const [overwriteOpen, setOverwriteOpen] = useState(false);
  const [computeError, setComputeError] = useState<string | null>(null);

  // Prepare-step state (ADR-0022): the recorded sweep + the operator knobs.
  const [frameTotal, setFrameTotal] = useState(0);
  const [frame, setFrame] = useState(0);
  const [stride, setStride] = useState(5);
  const [keyframeCap, setKeyframeCap] = useState(25);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [metrics, setMetrics] = useState<IntrinsicMetrics | null>(null);
  const [resultsView, setResultsView] = useState<ResultsView>('coverage');

  // Cameras rehydrate from the API after mount; if this screen mounted first, the
  // initial `active` is null. Once the list arrives (or the config changes so the
  // current selection goes stale), select a valid camera so its track tile resolves.
  const cameraNames = cameras.map((c) => c.name).join(',');
  useEffect(() => {
    if (cameras.length > 0 && !cameras.some((c) => c.name === active)) {
      setActive(cameras[0].name);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cameraNames]);

  // On camera switch: reset to the right sub-step for that camera's status.
  useEffect(() => {
    if (!active) return;
    setRecording(false);
    setStep(camera?.status === 'intrinsic_done' ? 'results' : 'capture');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // The live camera is only needed while capturing; Prepare/Results scrub the file.
  useEffect(() => {
    if (!active) return;
    setActiveIntrinsic(step === 'capture' ? active : null).catch(() => { });
  }, [active, step]);

  // On the Results step, load the persisted review metrics for the active camera.
  useEffect(() => {
    if (step !== 'results' || !active) {
      setMetrics(null);
      return;
    }
    let cancelled = false;
    fetchIntrinsicMetrics(active)
      .then((m) => !cancelled && setMetrics(m))
      .catch(() => !cancelled && setMetrics(null));
    return () => {
      cancelled = true;
    };
  }, [step, active]);

  useEffect(() => () => void setActiveIntrinsic(null).catch(() => { }), []);

  const trackRefs = useTracks([Track.Source.Camera], { onlySubscribed: true }).filter(
    isTrackReference,
  );
  const activeRef = trackRefs.find((r) => r.publication.trackName === active);

  const startRecording = async () => {
    if (!active) return;
    await startIntrinsic(active).catch(() => { });
    setRecording(true);
    setStep('capture');
  };

  // Stop the sweep and move to Prepare (replay + tuning) — no longer straight to compute.
  const stopRecording = async () => {
    if (!active) return;
    await stopIntrinsic(active).catch(() => { });
    setRecording(false);
    let total = 0;
    try {
      total = (await fetchIntrinsicFrameCount(active)).total;
    } catch {
      /* leave total at 0; the scrubber shows an empty-recording state */
    }
    setFrameTotal(total);
    setFrame(0);
    setTrimStart(0);
    setTrimEnd(Math.max(0, total - 1));
    setStride(5);
    setKeyframeCap(25);
    setStep('prepare');
  };

  const runCompute = async () => {
    if (!active) return;
    setStep('computing');
    setComputeError(null);
    try {
      await dispatch(
        computeIntrinsicThunk({
          camera: active,
          params: { stride, cap: keyframeCap, frame_start: trimStart, frame_end: trimEnd + 1 },
        }),
      ).unwrap();
      setStep('results');
    } catch (err) {
      setComputeError(err instanceof Error ? err.message : 'compute failed');
      setStep('prepare');
    }
  };

  // Re-record replaces the sweep (and, once recomputed, the calibration). On an
  // already-calibrated camera, confirm the overwrite first (ADR-0019).
  const reRecord = () => {
    if (camera?.status === 'intrinsic_done') {
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

  const nextCamera = () => {
    const idx = cameras.findIndex((c) => c.name === active);
    const next = cameras[idx + 1];
    if (next) setActive(next.name);
  };

  const scrubbing = step === 'prepare' || step === 'computing';

  return (
    <>
      <TelemetryListener />
      <SegmentedControl
        color="violet"
        value={active ?? ''}
        onChange={setActive}
        data={cameras.map((c) => ({
          label: c.status === 'intrinsic_done' ? `${c.name} ✓` : c.name,
          value: c.name,
        }))}
        mb="md"
      />
      <StepIndicator step={step} />

      <Box
        className="rc-camsetup-grid"
        style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 22 }}
      >
        <Box style={{ minWidth: 0, minHeight: 0, position: 'relative' }}>
          {step === 'results' ? (
            // ADR-0022 Results: toggle between the coverage heatmap and the 3D pose scene
            // (boards at their recovered poses); the result summary is on the right.
            metrics ? (
              <>
                {resultsView === 'poses' ? (
                  <Suspense
                    fallback={
                      <Center h="100%">
                        <Loader size="sm" />
                      </Center>
                    }
                  >
                    <PoseScene quads={metrics.board_quads} />
                  </Suspense>
                ) : (
                  <CoverageHeatmap grid={metrics.coverage} />
                )}
                <Box style={{ position: 'absolute', top: 38, right: 12, zIndex: 2 }}>
                  <SegmentedControl
                    size="xs"
                    value={resultsView}
                    onChange={(v) => setResultsView(v as ResultsView)}
                    data={[
                      { label: 'Coverage', value: 'coverage' },
                      { label: '3D poses', value: 'poses' },
                    ]}
                    styles={{
                      root: {
                        background: 'rgba(9,9,11,0.72)',
                        backdropFilter: 'blur(6px)',
                        border: '1px solid var(--rc-border)',
                      },
                    }}
                  />
                </Box>
              </>
            ) : (
              <Center h="100%">
                <Text c="dark.3" fz="0.84rem">
                  Loading review…
                </Text>
              </Center>
            )
          ) : scrubbing && active ? (
            <PrepareScrubber
              camera={active}
              total={frameTotal}
              frame={frame}
              onFrame={setFrame}
              trim={[trimStart, trimEnd]}
            />
          ) : activeRef ? (
            <CameraTile trackRef={activeRef} label={active ?? undefined} />
          ) : (
            <Center h="100%">
              <Text c="dark.3" fz="0.84rem">
                Waiting for {active ?? 'camera'} stream…
              </Text>
            </Center>
          )}
          {recording && step === 'capture' && (
            <Group
              gap={6}
              px={10}
              py={5}
              style={{
                position: 'absolute',
                top: 12,
                left: '50%',
                transform: 'translateX(-50%)',
                borderRadius: 20,
                background: 'rgba(9,9,11,0.7)',
              }}
            >
              <IconPlayerRecordFilled size={13} color="var(--rc-error)" />
              <Text fz="0.72rem" fw={600}>
                REC
              </Text>
            </Group>
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
          {step === 'results' && camera ? (
            <ResultPanel camera={camera} metrics={metrics} />
          ) : scrubbing ? (
            <PreparePanel
              frame={frame}
              trimStart={trimStart}
              trimEnd={trimEnd}
              stride={stride}
              keyframeCap={keyframeCap}
              onTrimStart={(n) => setTrimStart(Math.min(n, trimEnd))}
              onTrimEnd={(n) => setTrimEnd(Math.max(n, trimStart))}
              onStride={setStride}
              onCap={setKeyframeCap}
            />
          ) : (
            <GaugesPanel coverage={coverage} />
          )}

          {computeError && (
            <Text fz="0.72rem" c="var(--rc-error)" mt="sm">
              {computeError}
            </Text>
          )}

          <Box mt="auto" pt="md">
            {step === 'results' ? (
              <>
                <Button fullWidth onClick={nextCamera} disabled={cameras.findIndex((c) => c.name === active) >= cameras.length - 1}>
                  Validate → next camera
                </Button>
                <Button
                  fullWidth
                  variant="light"
                  color="gray"
                  mt="sm"
                  leftSection={<IconPlayerRecordFilled size={15} />}
                  onClick={reRecord}
                >
                  Re-record
                </Button>
              </>
            ) : scrubbing ? (
              <Button fullWidth loading={step === 'computing'} onClick={() => void runCompute()}>
                Compute
              </Button>
            ) : recording ? (
              <Button
                fullWidth
                color="red"
                leftSection={<IconPlayerStopFilled size={16} />}
                onClick={() => void stopRecording()}
              >
                Stop
              </Button>
            ) : (
              <Button
                fullWidth
                leftSection={<IconPlayerRecordFilled size={16} />}
                onClick={() => void startRecording()}
              >
                Start recording
              </Button>
            )}
          </Box>
        </Box>
      </Box>

      {/* Blocking compute modal (ADR-0019: capture released, solver runs). */}
      <Modal
        opened={step === 'computing'}
        onClose={() => { }}
        withCloseButton={false}
        closeOnClickOutside={false}
        closeOnEscape={false}
        centered
        title="Computing intrinsics"
      >
        <Text fz="0.8rem" c="dark.2" mb="md">
          {active} · selecting keyframes + solving from the recorded sweep
        </Text>
        <Progress value={100} animated striped mb="md" />
        <Group justify="center">
          <Loader size="sm" />
        </Group>
      </Modal>

      {/* Override double-validation (ADR-0019): re-recording overwrites the result. */}
      <Modal opened={overwriteOpen} onClose={() => setOverwriteOpen(false)} centered title={`Overwrite ${active} calibration?`}>
        <Group gap={10} mb="md" wrap="nowrap" align="flex-start">
          <IconAlertTriangle size={20} color="var(--rc-error)" style={{ flex: 'none', marginTop: 2 }} />
          <Text fz="0.84rem" c="dark.1">
            Re-recording {active} will replace the current intrinsics (error{' '}
            {camera?.calibration_error?.toFixed(2) ?? '—'} px) and overwrite the recorded video. The
            existing result is discarded and cannot be recovered.
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

// Intrinsic calibration per camera (ADR-0022 sub-FSM capture → prepare → computing →
// results): sweep the board (live burn-in + gauges), replay + tune the sampling in
// Prepare, compute from the recording, then review the result + coverage.
export function IntrinsicsScreen() {
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
        title="Intrinsics"
        subtitle="Per camera: capture a board sweep, prepare (replay + tune sampling), compute, then review the result."
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
          <IntrinsicsInner />
        </LiveKitRoom>
      )}
    </Box>
  );
}
