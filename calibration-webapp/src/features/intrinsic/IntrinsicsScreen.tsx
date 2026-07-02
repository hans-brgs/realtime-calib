import {
  isTrackReference,
  LiveKitRoom,
  useDataChannel,
  useTracks,
} from '@livekit/components-react';
import { Box, Center, Group, Loader, SegmentedControl, Text } from '@mantine/core';
import { Track } from 'livekit-client';
import { useEffect, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { ScreenHeader } from '@/components/ScreenHeader';
import { CameraTile } from '@/features/preview/CameraTile';
import { selectSession } from '@/features/session/sessionSlice';
import {
  type CoverageMetrics,
  coverageReceived,
  selectCoverage,
} from '@/features/telemetry/telemetrySlice';
import { fetchToken, setActiveIntrinsic } from '@/transport/httpClient';

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
    <Box
      style={{
        border: '1px solid var(--rc-border)',
        borderRadius: 'var(--mantine-radius-lg)',
        background: 'var(--rc-panel)',
        padding: 16,
      }}
    >
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
        <Group gap={7}>
          <Box
            w={8}
            h={8}
            style={{
              borderRadius: '50%',
              background: coverage?.sharpness_ok ? 'var(--rc-success)' : 'var(--rc-error)',
            }}
          />
          <Text fz="0.76rem" style={{ color: coverage?.sharpness_ok ? 'var(--rc-success)' : 'var(--rc-error)' }}>
            {coverage?.sharpness_ok ? 'sharp' : 'too blurry'}
          </Text>
        </Group>
      </Group>

      <Group justify="space-between" mt="sm">
        <Text fz="0.72rem" c="dark.2">
          Tilt vs frontal
        </Text>
        <Text fz="0.78rem" fw={600} className="rc-tnum">
          {coverage?.tilt_deg != null ? `${Math.round(coverage.tilt_deg)}°` : '—'}
          <Text span fz="0.66rem" c="dark.3" inherit>
            {' '}· vary 0→45°
          </Text>
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
    </Box>
  );
}

// Receives coverage_metrics on the data channel and routes them to the store.
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
  const session = useAppSelector(selectSession);
  const cameras = session?.cameras ?? [];
  const [active, setActive] = useState<string | null>(cameras[0]?.name ?? null);
  const coverage = useAppSelector(selectCoverage(active));

  useEffect(() => {
    if (active) {
      setActiveIntrinsic(active).catch(() => {});
    }
  }, [active]);

  useEffect(() => () => void setActiveIntrinsic(null).catch(() => {}), []);

  const trackRefs = useTracks([Track.Source.Camera], { onlySubscribed: true }).filter(
    isTrackReference,
  );
  const activeRef = trackRefs.find((r) => r.publication.trackName === active);

  return (
    <>
      <TelemetryListener />
      <SegmentedControl
        color="violet"
        value={active ?? ''}
        onChange={setActive}
        data={cameras.map((c) => ({ label: c.name, value: c.name }))}
        mb="md"
      />
      <Box
        className="rc-camsetup-grid"
        style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 22 }}
      >
        <Box style={{ minWidth: 0, minHeight: 0 }}>
          {activeRef ? (
            <CameraTile trackRef={activeRef} label={active ?? undefined} />
          ) : (
            <Center h="100%">
              <Text c="dark.3" fz="0.84rem">
                Waiting for {active ?? 'camera'} stream…
              </Text>
            </Center>
          )}
        </Box>
        <Box style={{ minHeight: 0, overflowY: 'auto' }}>
          <GaugesPanel coverage={coverage} />
        </Box>
      </Box>
    </>
  );
}

// Minimal intrinsic recording view (Phase 3.5 vertical slice): pick a camera, see
// its burn-in feed and the live coverage gauges. Compute/review sub-steps come later.
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
        subtitle="Pick a camera and sweep the board across its field of view; the gauges below are indicative — keyframe selection happens at compute (ADR-0019)."
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
