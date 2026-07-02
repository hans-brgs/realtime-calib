import {
  isTrackReference,
  LiveKitRoom,
  useDataChannel,
  useTracks,
} from '@livekit/components-react';
import { Box, Button, Center, Group, Loader, Modal, Progress, SegmentedControl, Text } from '@mantine/core';
import { IconAlertTriangle, IconPlayerRecordFilled, IconPlayerStopFilled } from '@tabler/icons-react';
import { Track } from 'livekit-client';
import { useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { ScreenHeader } from '@/components/ScreenHeader';
import { CameraTile } from '@/features/preview/CameraTile';
import { computeIntrinsicThunk, selectSession } from '@/features/session/sessionSlice';
import {
  type CoverageMetrics,
  coverageReceived,
  selectCoverage,
} from '@/features/telemetry/telemetrySlice';
import { fetchToken, setActiveIntrinsic, startIntrinsic, stopIntrinsic } from '@/transport/httpClient';
import type { CameraConfig } from '@/transport/types';

type Step = 'record' | 'computing' | 'review';

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

function ResultPanel({ camera }: { camera: CameraConfig }) {
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
          {camera.matrix ? `${camera.matrix[0][0].toFixed(0)}, ${camera.matrix[1][1].toFixed(0)}` : '—'}
        </Text>
      </Group>
    </>
  );
}

const STEPS: { key: Step; label: string }[] = [
  { key: 'record', label: 'Record' },
  { key: 'computing', label: 'Compute' },
  { key: 'review', label: 'Review' },
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

  const [step, setStep] = useState<Step>('record');
  const [recording, setRecording] = useState(false);
  const [overwriteOpen, setOverwriteOpen] = useState(false);
  const [computeError, setComputeError] = useState<string | null>(null);

  // On camera switch: live overlay on that camera + derive the sub-step from its status.
  useEffect(() => {
    if (!active) return;
    setActiveIntrinsic(active).catch(() => {});
    setRecording(false);
    setStep(camera?.status === 'intrinsic_done' ? 'review' : 'record');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  useEffect(() => () => void setActiveIntrinsic(null).catch(() => {}), []);

  const trackRefs = useTracks([Track.Source.Camera], { onlySubscribed: true }).filter(
    isTrackReference,
  );
  const activeRef = trackRefs.find((r) => r.publication.trackName === active);

  const runCompute = async () => {
    if (!active) return;
    setStep('computing');
    setComputeError(null);
    try {
      await stopIntrinsic(active);
      await dispatch(computeIntrinsicThunk(active)).unwrap();
      setStep('review');
    } catch (err) {
      setComputeError(err instanceof Error ? err.message : 'compute failed');
      setStep('record');
    } finally {
      setRecording(false);
    }
  };

  const startRecording = async () => {
    if (!active) return;
    await startIntrinsic(active).catch(() => {});
    setRecording(true);
    setStep('record');
  };

  const recompute = () => {
    if (camera?.status === 'intrinsic_done') {
      setOverwriteOpen(true);
    } else {
      void runCompute();
    }
  };

  const nextCamera = () => {
    const idx = cameras.findIndex((c) => c.name === active);
    const next = cameras[idx + 1];
    if (next) setActive(next.name);
  };

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
          {activeRef ? (
            <CameraTile trackRef={activeRef} label={active ?? undefined} />
          ) : (
            <Center h="100%">
              <Text c="dark.3" fz="0.84rem">
                Waiting for {active ?? 'camera'} stream…
              </Text>
            </Center>
          )}
          {recording && (
            <Group
              gap={6}
              px={10}
              py={5}
              style={{ position: 'absolute', top: 12, left: 12, borderRadius: 20, background: 'rgba(9,9,11,0.7)' }}
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
          {step === 'review' && camera ? <ResultPanel camera={camera} /> : <GaugesPanel coverage={coverage} />}

          {computeError && (
            <Text fz="0.72rem" c="var(--rc-error)" mt="sm">
              {computeError}
            </Text>
          )}

          <Box mt="auto" pt="md">
            {step === 'review' ? (
              <>
                <Button fullWidth onClick={nextCamera} disabled={cameras.findIndex((c) => c.name === active) >= cameras.length - 1}>
                  Validate → next camera
                </Button>
                <Button fullWidth variant="light" color="gray" mt="sm" onClick={recompute}>
                  Recompute
                </Button>
              </>
            ) : recording ? (
              <Button
                fullWidth
                color="red"
                leftSection={<IconPlayerStopFilled size={16} />}
                onClick={() => void runCompute()}
              >
                Stop &amp; compute
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
        onClose={() => {}}
        withCloseButton={false}
        closeOnClickOutside={false}
        closeOnEscape={false}
        centered
        title="Computing intrinsics"
      >
        <Text fz="0.8rem" c="dark.2" mb="md">
          {active} · calibrateCameraCharucoExtended
        </Text>
        <Progress value={100} animated striped mb="md" />
        <Group justify="center">
          <Loader size="sm" />
        </Group>
      </Modal>

      {/* Override double-validation (ADR-0019): recompute overwrites the result. */}
      <Modal opened={overwriteOpen} onClose={() => setOverwriteOpen(false)} centered title={`Overwrite ${active} calibration?`}>
        <Group gap={10} mb="md" wrap="nowrap" align="flex-start">
          <IconAlertTriangle size={20} color="var(--rc-error)" style={{ flex: 'none', marginTop: 2 }} />
          <Text fz="0.84rem" c="dark.1">
            Recomputing replaces the current intrinsics for {active} (error{' '}
            {camera?.calibration_error?.toFixed(2) ?? '—'} px). The existing result is discarded. The
            recorded video is kept.
          </Text>
        </Group>
        <Group justify="flex-end">
          <Button variant="default" onClick={() => setOverwriteOpen(false)}>
            Cancel
          </Button>
          <Button
            color="red"
            onClick={() => {
              setOverwriteOpen(false);
              void runCompute();
            }}
          >
            Overwrite &amp; recompute
          </Button>
        </Group>
      </Modal>
    </>
  );
}

// Intrinsic calibration per camera (ADR-0019 sub-FSM record → compute → review):
// sweep the board (live burn-in + gauges), compute from the recording, review the RMSE.
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
        subtitle="Per camera: record a board sweep, compute, then review the reprojection error before validating."
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
