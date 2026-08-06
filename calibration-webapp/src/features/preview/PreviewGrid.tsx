import { isTrackReference, type TrackReference, useTracks } from '@livekit/components-react';
import { Center, Text } from '@mantine/core';
import { Track } from 'livekit-client';
import type { CSSProperties } from 'react';

import { HERO_MEDIA_CEILING, useCompactLayout } from '@/components/layout/useCompactLayout';
import { CameraTile } from '@/features/preview/CameraTile';

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

  if (cameras.length === 0) {
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
  const tiles = cameras.map((ref) => {
    const placement = arrange?.(trackName(ref)) ?? null;
    return {
      ref,
      label: placement?.label,
      sortIndex: placement?.sortIndex ?? Number.MAX_SAFE_INTEGER,
    };
  });
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

  return (
    <div style={containerStyle}>
      {tiles.map((tile) => (
        <div key={tile.ref.publication.trackSid} style={cellStyle}>
          <CameraTile trackRef={tile.ref} label={tile.label} />
        </div>
      ))}
    </div>
  );
}

// Kept as the historical name used by the screens; the room now lives in
// RoomProvider (App level), so this is just the grid.
export function PreviewGrid({ arrange }: { arrange?: TrackArrangement } = {}) {
  return <CameraGrid arrange={arrange} />;
}
