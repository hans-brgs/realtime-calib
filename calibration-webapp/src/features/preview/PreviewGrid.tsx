import { isTrackReference, type TrackReference, useTracks } from '@livekit/components-react';
import { Alert, Box, Center, Group, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { Track } from 'livekit-client';
import type { CSSProperties, ReactNode } from 'react';

import { HERO_MEDIA_CEILING, useCompactLayout } from '@/components/layout/useCompactLayout';
import { CameraHealthOverlay } from '@/features/preview/CameraHealthOverlay';
import { CameraTile } from '@/features/preview/CameraTile';
import { failingCameras, healthColor, useCameraHealth } from '@/features/preview/useCameraHealth';
import type { CameraCaptureState } from '@/features/telemetry/telemetrySlice';

// Resolves a track's display position + label from the operator's pending index order
// (so the preview reflects a drag-reorder before it is applied). Returns null to leave
// a track in its natural place with its published name.
export type TrackArrangement = (trackName: string) => { sortIndex: number; label: string } | null;

// Cameras are keyed by the published track name (cam_i): a single publisher
// participant carries all tracks (ADR-0018), so the participant identity no longer
// identifies a camera.
const trackName = (ref: TrackReference): string => ref.publication.trackName;

// Camera tiles. Desktop / landscape: a grid that fills the area with NO scroll
// (4 cameras -> 2x2); each tile letterboxes its frame (objectFit: contain) so the
// camera ratio is respected with black bars rather than scrolling or cropping. Phone
// / portrait: a single scrolling column of aspect-ratio tiles. See
// multi-camera-preview / wizard-navigation. Consumes the app-level room context
// (RoomProvider in App.tsx): screens never mount their own LiveKitRoom, so
// navigating between steps never tears down the WebRTC session.
export function CameraGrid({ arrange }: { arrange?: TrackArrangement }) {
  const compact = useCompactLayout();
  const trackRefs = useTracks([Track.Source.Camera], { onlySubscribed: true });
  const cameras = trackRefs.filter(isTrackReference);
  const health = useCameraHealth();
  const failing = failingCameras(health);
  // A failing camera normally keeps a tile: its track is published (muted) before
  // the open is even attempted. One whose track never reached us still gets a tile,
  // so a broken camera can never look the same as an unconfigured one (#46).
  const published = new Set(cameras.map(trackName));
  const orphans = failing.filter(([name]) => !published.has(name));

  if (cameras.length === 0 && orphans.length === 0) {
    return (
      <Center h="100%">
        <Text c="dark.3" fz="0.84rem">
          Waiting for camera streams…
        </Text>
      </Center>
    );
  }

  // Apply the pending arrangement (reorder + relabel). Keys stay the track sid, so
  // reordering moves the video nodes without remounting them (no stream interruption).
  const tiles: { key: string; node: ReactNode; sortIndex: number }[] = cameras.map((ref) => {
    const placement = arrange?.(trackName(ref)) ?? null;
    return {
      key: ref.publication.trackSid,
      node: <CameraTile trackRef={ref} label={placement?.label} />,
      sortIndex: placement?.sortIndex ?? Number.MAX_SAFE_INTEGER,
    };
  });
  for (const [name, state] of orphans) {
    const placement = arrange?.(name) ?? null;
    tiles.push({
      key: `missing-${name}`,
      node: <MissingCameraTile name={placement?.label ?? name} health={state} />,
      sortIndex: placement?.sortIndex ?? Number.MAX_SAFE_INTEGER,
    });
  }
  if (arrange) {
    tiles.sort((a, b) => a.sortIndex - b.sortIndex);
  }

  const cols = compact ? 1 : Math.ceil(Math.sqrt(tiles.length));
  const rows = Math.ceil(tiles.length / cols);

  const containerStyle: CSSProperties = compact
    ? { display: 'flex', flexDirection: 'column', gap: 12, height: '100%', overflowY: 'auto' }
    : {
        display: 'grid',
        gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gridTemplateRows: `repeat(${rows}, 1fr)`,
        gap: 12,
        height: '100%',
        overflow: 'hidden',
      };
  const cellStyle: CSSProperties = compact
    ? // Same treatment as the single-media hero (ADR-0041): full width, 16:9, capped
      // by the media ceiling. In landscape a full-width 16:9 tile is TALLER than the
      // viewport (~474px on a 402px-high phone), so without the cap no tile ever fits
      // on screen; capped, each tile pillarboxes its frame between black side bands.
      // The explicit width matters: with it left auto, the max-height would transfer
      // into a max-width through the aspect ratio and shrink + left-align the tile.
      { width: '100%', aspectRatio: '16 / 9', maxHeight: HERO_MEDIA_CEILING, flex: '0 0 auto' }
    : { minWidth: 0, minHeight: 0 };

  const grid = (
    <div style={containerStyle}>
      {tiles.map((tile) => (
        <div key={tile.key} style={cellStyle}>
          {tile.node}
        </div>
      ))}
    </div>
  );
  if (failing.length === 0) return grid;
  return (
    <Box style={{ display: 'flex', flexDirection: 'column', gap: 12, height: '100%' }}>
      <CameraDegradedBanner failing={failing} />
      <Box style={{ flex: 1, minHeight: 0 }}>{grid}</Box>
    </Box>
  );
}

// Rig-level warning above the tiles (spec multi-camera-preview): with several cameras
// on screen, one dark tile among four is easy to miss, and the reason belongs in words.
function CameraDegradedBanner({ failing }: { failing: [string, CameraCaptureState][] }) {
  const [first] = failing;
  return (
    <Alert
      color="red"
      variant="light"
      p="xs"
      icon={<IconAlertTriangle size={16} />}
      style={{ flex: 'none' }}
      title={
        failing.length === 1
          ? `${first[0]} unavailable`
          : `${failing.length} cameras unavailable: ${failing.map(([name]) => name).join(', ')}`
      }
    >
      <Text fz="0.74rem">
        {failing.length === 1 && first[1].reason ? `${first[1].reason}. ` : ''}
        Check the cable and the USB port — the service keeps retrying on its own.
      </Text>
    </Alert>
  );
}

// Stand-in tile for a failing camera whose track never reached the webapp: same frame
// and label as a CameraTile, the error overlay instead of video.
function MissingCameraTile({ name, health }: { name: string; health: CameraCaptureState }) {
  return (
    <Box
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        minHeight: 0,
        borderRadius: 12,
        overflow: 'hidden',
        border: '1px solid var(--mantine-color-dark-4)',
        background: 'var(--rc-page)',
      }}
    >
      <CameraHealthOverlay health={health} />
      <Group
        gap={6}
        wrap="nowrap"
        style={{
          position: 'absolute',
          top: 8,
          left: 8,
          background: 'rgba(0, 0, 0, 0.55)',
          borderRadius: 6,
          padding: '3px 8px',
        }}
      >
        <Box w={7} h={7} style={{ borderRadius: '50%', background: healthColor(health) }} />
        <Text fz="0.69rem" c="dark.0">
          {name}
        </Text>
      </Group>
    </Box>
  );
}

// Kept as the historical name used by the screens; the room now lives in
// RoomProvider (App level), so this is just the grid.
export function PreviewGrid({ arrange }: { arrange?: TrackArrangement } = {}) {
  return <CameraGrid arrange={arrange} />;
}
