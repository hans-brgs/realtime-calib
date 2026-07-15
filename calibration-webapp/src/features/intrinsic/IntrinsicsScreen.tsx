import { isTrackReference, useTracks } from '@livekit/components-react';
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
  IconCheck,
  IconPlayerRecordFilled,
  IconPlayerStopFilled,
} from '@tabler/icons-react';
import { Track } from 'livekit-client';
import { lazy, Suspense, useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { PhaseStepper } from '@/components/PhaseStepper';
import { ScreenHeader } from '@/components/ScreenHeader';
import { CaptureWizardLayout } from '@/features/capture/CaptureWizardLayout';
import { TranscodePreparingModal } from '@/features/capture/TranscodePreparingModal';
import { useCaptureWizard } from '@/features/capture/useCaptureWizard';
import { usePreviewTranscode } from '@/features/capture/usePreviewTranscode';
import { CoverageHeatmap } from '@/features/intrinsic/CoverageHeatmap';
import { PrepareScrubber } from '@/features/intrinsic/PrepareScrubber';
import { CameraTile } from '@/features/preview/CameraTile';
import {
  computeIntrinsicThunk,
  selectSession,
  validateIntrinsicThunk,
} from '@/features/session/sessionSlice';
import { selectDefaults } from '@/features/session/defaultsSlice';
import { type CoverageMetrics, selectCoverage } from '@/features/telemetry/telemetrySlice';
import {
  errorMessage,
  fetchIntrinsicPreviewStatus,
  retryIntrinsicPreview,
  fetchIntrinsicMetrics,
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

// Board coverage (extrapolated area / frame) → colour band; green >= 0.50 = calib.io.
function fillColor(coverage: number): string {
  if (coverage < 0.15) return 'var(--rc-error)';
  if (coverage < 0.3) return '#fb923c';
  if (coverage < 0.5) return 'var(--rc-warning)';
  return 'var(--rc-success)';
}

function Gauge({
  label,
  value,
  pct,
  color,
}: {
  label: string;
  value: string;
  pct: number;
  color: string;
}) {
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
      <Box
        style={{ height: 8, borderRadius: 6, background: 'var(--rc-input)', overflow: 'hidden' }}
      >
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
          style={{
            borderRadius: 20,
            background: 'rgba(251,191,36,0.14)',
            color: 'var(--rc-warning)',
          }}
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
        <Text
          fz="0.76rem"
          style={{ color: coverage?.sharpness_ok ? 'var(--rc-success)' : 'var(--rc-error)' }}
        >
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

function ResultPanel({
  camera,
  metrics,
}: {
  camera: CameraConfig;
  metrics: IntrinsicMetrics | null;
}) {
  const coveragePct = metrics ? Math.round(metrics.image_coverage * 100) : null;
  const bins = metrics?.orientation_bins ?? null;
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
          c={
            coveragePct == null
              ? undefined
              : coveragePct >= 80
                ? 'var(--rc-success)'
                : 'var(--rc-warning)'
          }
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
          Keyframes used
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
          {camera.matrix
            ? `${camera.matrix[0][0].toFixed(1)}, ${camera.matrix[1][1].toFixed(1)}`
            : '—'}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Principal point (cx, cy)
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {camera.matrix
            ? `${camera.matrix[0][2].toFixed(1)}, ${camera.matrix[1][2].toFixed(1)}`
            : '—'}
        </Text>
      </Group>
      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Distortion (k1, k2)
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {camera.distortions
            ? `${camera.distortions[0].toFixed(3)}, ${camera.distortions[1].toFixed(3)}`
            : '—'}
        </Text>
      </Group>
      <Text fz="0.66rem" c="dark.3" mt="md">
        Calibrated at {camera.width * camera.resize_factor}×{camera.height * camera.resize_factor}
        {camera.resize_factor !== 1
          ? ` (native ${camera.width}×${camera.height}, ×${camera.resize_factor})`
          : ''}
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
  // Backend-served bounds (GET /defaults, ADR-0036) — [min, max] per knob.
  strideBounds: [number, number];
  capBounds: [number, number];
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
  strideBounds,
  capBounds,
  onTrimStart,
  onTrimEnd,
  onStride,
  onCap,
}: PreparePanelProps) {
  // "1 frame every N" over the trim span: what the compute will actually detect.
  const span = Math.max(0, trimEnd + 1 - trimStart);
  const analyzed = span > 0 ? Math.ceil(span / Math.max(1, stride)) : 0;
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
        description={`${analyzed} frames will be analyzed`}
        value={stride}
        onChange={(v) => onStride(Math.max(strideBounds[0], Number(v) || strideBounds[0]))}
        min={strideBounds[0]}
        max={strideBounds[1]}
        mb="md"
      />
      <NumberInput
        label="Keyframe cap (max kept)"
        value={keyframeCap}
        onChange={(v) => onCap(Math.max(capBounds[0], Number(v) || capBounds[0]))}
        min={capBounds[0]}
        max={capBounds[1]}
        mb="md"
      />
      <Text fz="0.66rem" c="dark.3">
        Fewer keyframes → faster compute, potentially lower coverage.
      </Text>
    </>
  );
}

const PHASES = [
  { key: 'capture', label: 'Capture', sub: 'live capture + video' },
  { key: 'prepare', label: 'Prepare', sub: 'replay + trim + stride' },
  { key: 'computing', label: 'Computing', sub: 'keyframes + solve' },
  { key: 'review', label: 'Results', sub: 'params + coverage' },
];

function IntrinsicsInner() {
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  // Imported session (ADR-0031): capture is neutralised — the sub-wizard starts
  // on Prepare (the recordings came with the archive) and never offers recording.
  const imported = session?.mode === 'load-from-files';
  const cameras = session?.cameras ?? [];
  const [active, setActive] = useState<string | null>(cameras[0]?.name ?? null);
  const coverage = useAppSelector(selectCoverage(active));
  const camera = cameras.find((c) => c.name === active) ?? null;

  // Backend-served knob defaults/bounds (GET /defaults, ADR-0036).
  const defaults = useAppSelector(selectDefaults);

  // Prepare-step state (ADR-0022): the recorded sweep + the operator knobs.
  // Initial stride/cap are the structural minima; enterPrepare re-seeds them from
  // the served defaults (no hardcoded copy of the tuning values here).
  const [frameTotal, setFrameTotal] = useState(0);
  const [frame, setFrame] = useState(0);
  const [stride, setStride] = useState(1);
  const [keyframeCap, setKeyframeCap] = useState(6);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [metrics, setMetrics] = useState<IntrinsicMetrics | null>(null);
  const [resultsView, setResultsView] = useState<ResultsView>('coverage');

  // Prepare-step knobs setup from the recorded sweep (ADR-0022). The step move to
  // Prepare is driven by the wizard (transcode onReady), not here.
  const enterPrepare = (total: number) => {
    setFrameTotal(total);
    setFrame(0);
    setTrimStart(0);
    setTrimEnd(Math.max(0, total - 1));
    if (defaults) {
      setStride(defaults.intrinsic_stride);
      setKeyframeCap(defaults.intrinsic_cap);
    }
  };

  // Shared capture sub-wizard (D5): capture -> prepare -> computing -> review.
  const wizard = useCaptureWizard({
    initialStep: imported ? 'prepare' : 'capture',
    start: async () => {
      if (!active) throw new Error('no active camera');
      await startIntrinsic(active);
    },
    stop: async () => {
      if (active) await stopIntrinsic(active).catch(() => {});
    },
    afterStop: () => transcode.run(),
    compute: async () => {
      if (!active) throw new Error('no active camera');
      await dispatch(
        computeIntrinsicThunk({
          camera: active,
          params: { stride, cap: keyframeCap, frame_start: trimStart, frame_end: trimEnd + 1 },
        }),
      ).unwrap();
    },
  });

  // Background preview transcode (ADR-0027) behind a blocking modal, then enter Prepare.
  const transcode = usePreviewTranscode({
    poll: async () => {
      if (!active) throw new Error('no active camera');
      return fetchIntrinsicPreviewStatus(active);
    },
    onReady: (status) => {
      enterPrepare(status.state === 'done' ? status.frames : 0);
      wizard.toPrepare();
    },
    getError: (status) => status.error,
    retryJob: async () => {
      if (active) await retryIntrinsicPreview(active);
    },
  });

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

  // On camera switch (and first mount): reset to the right sub-step for that
  // camera's status. This runs after the initialStep, so it is THE authority.
  useEffect(() => {
    if (!active) return;
    // Land on Review whenever intrinsics EXIST (matrix present) — the status moves on
    // to 'extrinsic_done' later, but the calibration is not lost. reset also stops REC.
    // Imported session: no capture sub-step — land on Prepare and open the recording
    // (the transcode poll fills the scrubber; usually already done, kicked at import).
    const review = camera?.matrix != null;
    wizard.reset(review ? 'review' : imported ? 'prepare' : 'capture');
    if (!review && imported) {
      void transcode.run();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // The live camera is only needed while capturing; Prepare/Review scrub the file.
  useEffect(() => {
    if (!active) return;
    setActiveIntrinsic(wizard.step === 'capture' ? active : null).catch(() => {});
  }, [active, wizard.step]);

  // On the Review step, load the persisted review metrics for the active camera.
  useEffect(() => {
    if (wizard.step !== 'review' || !active) {
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
  }, [wizard.step, active]);

  useEffect(() => () => void setActiveIntrinsic(null).catch(() => {}), []);

  const trackRefs = useTracks([Track.Source.Camera], { onlySubscribed: true }).filter(
    isTrackReference,
  );
  const activeRef = trackRefs.find((r) => r.publication.trackName === active);

  const nextCamera = () => {
    const idx = cameras.findIndex((c) => c.name === active);
    const next = cameras[idx + 1];
    if (next) setActive(next.name);
  };

  const isLastCamera = cameras.findIndex((c) => c.name === active) >= cameras.length - 1;
  const allCalibrated =
    cameras.length > 0 &&
    cameras.every((c) => c.status === 'intrinsic_done' || c.status === 'extrinsic_done');

  // On the last camera, "Validate" signs off the whole intrinsic step: advance the
  // persisted step to extrinsic capture and let the wizard rail follow (no explicit
  // navigation needed, spec wizard-navigation).
  const validateAndAdvance = async () => {
    wizard.setMessage(null);
    try {
      await dispatch(validateIntrinsicThunk()).unwrap();
    } catch (err) {
      wizard.setMessage(errorMessage(err, 'validate failed'));
    }
  };

  const scrubbing = wizard.step === 'prepare' || wizard.step === 'computing';

  return (
    <>
      <CaptureWizardLayout
        top={
          <SegmentedControl
            color="violet"
            value={active ?? ''}
            onChange={setActive}
            data={cameras.map((c) => ({
              // ✓ = has intrinsics (matrix present): both intrinsic_done AND extrinsic_done.
              label: c.matrix != null ? `${c.name} ✓` : c.name,
              value: c.name,
            }))}
            mb="md"
          />
        }
        stepper={
          <PhaseStepper
            // Imported session: the capture phase does not exist (ADR-0031).
            phases={imported ? PHASES.filter((p) => p.key !== 'capture') : PHASES}
            current={wizard.step}
          />
        }
        main={
          <>
            {wizard.step === 'review' ? (
              // ADR-0022 Review: toggle between the coverage heatmap and the 3D pose scene
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
            {wizard.recording && wizard.step === 'capture' && (
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
          </>
        }
        panel={
          wizard.step === 'review' && camera ? (
            <ResultPanel camera={camera} metrics={metrics} />
          ) : scrubbing ? (
            <PreparePanel
              frame={frame}
              trimStart={trimStart}
              trimEnd={trimEnd}
              stride={stride}
              keyframeCap={keyframeCap}
              strideBounds={defaults?.intrinsic_stride_bounds ?? [1, 30]}
              capBounds={defaults?.intrinsic_cap_bounds ?? [6, 100]}
              onTrimStart={(n) => setTrimStart(Math.min(n, trimEnd))}
              onTrimEnd={(n) => setTrimEnd(Math.max(n, trimStart))}
              onStride={setStride}
              onCap={setKeyframeCap}
            />
          ) : (
            <GaugesPanel coverage={coverage} />
          )
        }
        action={
          wizard.step === 'review' ? (
            <>
              {isLastCamera ? (
                <Button
                  fullWidth
                  leftSection={<IconCheck size={15} />}
                  onClick={() => void validateAndAdvance()}
                  disabled={!allCalibrated}
                >
                  Validate → Extrinsics
                </Button>
              ) : (
                <Button fullWidth onClick={nextCamera}>
                  Validate → next camera
                </Button>
              )}
              {/* Back to Prepare on the SAME recording: retune trim/stride/cap,
                  then Compute again (the preview mp4 is already there). */}
              <Button fullWidth variant="light" mt="sm" onClick={() => void transcode.run()}>
                Recompute (tune again)
              </Button>
              {/* No re-record on an imported session (ADR-0031): there is no live
                  camera, and starting a recording would overwrite the imported video. */}
              {!imported && (
                <Button
                  fullWidth
                  variant="light"
                  color="gray"
                  mt="sm"
                  leftSection={<IconPlayerRecordFilled size={15} />}
                  onClick={wizard.reRecord}
                >
                  Re-record
                </Button>
              )}
            </>
          ) : scrubbing ? (
            <Button
              fullWidth
              loading={wizard.step === 'computing'}
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
              Start recording
            </Button>
          )
        }
        message={wizard.message}
      />

      {/* Blocking compute modal (ADR-0019: capture released, solver runs). */}
      <Modal
        opened={wizard.step === 'computing'}
        onClose={() => {}}
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

      {/* Preview transcode (ADR-0027): blocks the Stop -> Prepare transition. */}
      <TranscodePreparingModal
        status={transcode.status}
        onRetry={() => void transcode.retry()}
        onClose={transcode.dismiss}
      />

      {/* Override double-validation (ADR-0019): re-recording overwrites the result. */}
      <Modal
        opened={wizard.overwriteOpen}
        onClose={wizard.cancelOverwrite}
        centered
        title={`Overwrite ${active} calibration?`}
      >
        <Group gap={10} mb="md" wrap="nowrap" align="flex-start">
          <IconAlertTriangle
            size={20}
            color="var(--rc-error)"
            style={{ flex: 'none', marginTop: 2 }}
          />
          <Text fz="0.84rem" c="dark.1">
            Re-recording {active} will replace the current intrinsics (error{' '}
            {camera?.calibration_error?.toFixed(2) ?? '—'} px) and overwrite the recorded video. The
            existing result is discarded and cannot be recovered.
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

// Intrinsic calibration per camera (ADR-0022 sub-FSM capture → prepare → computing →
// results): sweep the board (live burn-in + gauges), replay + tune the sampling in
// Prepare, compute from the recording, then review the result + coverage. The
// LiveKit room lives at the App level (RoomProvider) — this screen only consumes it.
export function IntrinsicsScreen() {
  return (
    <Box p={{ base: 'md', sm: 'xl' }} h="100%" style={{ display: 'flex', flexDirection: 'column' }}>
      <ScreenHeader
        title="Intrinsics"
        subtitle="Per camera: capture a board sweep, prepare (replay + tune sampling), compute, then review the result."
      />
      <Box style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <IntrinsicsInner />
      </Box>
    </Box>
  );
}
