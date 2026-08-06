import { ActionIcon, Box, Center, Group, Slider, Text } from '@mantine/core';
import { IconPlayerPauseFilled, IconPlayerPlayFilled } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';

import { useCompactLayout } from '@/components/layout/useCompactLayout';
import { intrinsicPreviewUrl } from '@/transport/httpClient';

// Prepare-step replay (ADR-0027/0037): a native <video> over the CFR-retimed
// preview mp4 — frame i sits exactly at (i + 0.5) / fps, where fps is SERVED by
// the transcode status (the recording's own rate — dynamic contract), so the
// slider and trim bounds map 1:1 onto the mkv indices the compute reads and
// playback speed is true. Play loops in the trim.
interface PrepareScrubberProps {
  camera: string;
  total: number;
  fps: number; // index <-> time rate served by the transcode status
  version: string; // cache-buster served by the transcode status (stale-video guard)
  frame: number;
  onFrame: (index: number) => void;
  trim: [number, number]; // inclusive [start, end], drawn as slider marks
}

const frameTime = (index: number, fps: number): number => (index + 0.5) / fps;

export function PrepareScrubber({
  camera,
  total,
  fps,
  version,
  frame,
  onFrame,
  trim,
}: PrepareScrubberProps) {
  const video = useRef<HTMLVideoElement>(null);
  const reported = useRef(-1);
  const compact = useCompactLayout();
  const [playing, setPlaying] = useState(false);
  const [start, end] = trim;
  const max = Math.max(0, total - 1);

  // Paused: the playhead follows the parent's frame (slider, trim clicks).
  useEffect(() => {
    const element = video.current;
    if (element && !playing) {
      element.currentTime = frameTime(Math.min(frame, max), fps);
    }
  }, [frame, max, playing, fps]);

  // Playing: the video clock leads; report indices up, loop inside the trim.
  useEffect(() => {
    const element = video.current;
    if (!element || !playing || total === 0) return;
    void element.play();
    const id = window.setInterval(() => {
      const index = Math.min(Math.floor(element.currentTime * fps), max);
      if (index >= end) {
        element.currentTime = frameTime(start, fps); // loop back to the trim start
        reported.current = start;
        onFrame(start);
      } else if (index !== reported.current) {
        reported.current = index;
        onFrame(index);
      }
    }, 1000 / fps);
    return () => {
      window.clearInterval(id);
      element.pause();
    };
  }, [playing, start, end, max, total, onFrame, fps]);

  if (total === 0) {
    return (
      <Center
        h="100%"
        style={{ border: '1px dashed var(--rc-border)', borderRadius: 'var(--mantine-radius-md)' }}
      >
        <Text c="dark.3" fz="0.84rem">
          No recorded frames to replay.
        </Text>
      </Center>
    );
  }

  // One control row, two placements. Desktop: below the video, where space is ample.
  // Compact: OVERLAID on the video's bottom edge (standard player chrome) — inside the
  // fixed 16:9 hero box a below-the-video row stole ~46px from the frame, so the video
  // never reached full width in portrait and wore side bands there. Overlaid, the video
  // owns the whole box: full width in portrait, pillarboxed only in landscape where the
  // box is height-capped. (The 3D review keeps its row BELOW the canvas on purpose — an
  // overlay there would steal the rotate gesture along the bottom edge.)
  // No "frame" prefix and no fixed text width: the slider gets every spare pixel.
  // Overlaid controls read as player chrome, not app buttons: a ghost white glyph
  // instead of the filled violet pill (same hit area, half the visual bulk), and
  // white shadowed text — the grey ramp (dark.2) vanished over bright footage.
  const controls = (
    <>
      <ActionIcon
        variant={compact ? 'transparent' : 'light'}
        color="violet"
        size="lg"
        aria-label={playing ? 'Pause' : 'Play'}
        style={
          compact ? { color: '#fff', filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.8))' } : undefined
        }
        onClick={() => setPlaying((p) => !p)}
      >
        {playing ? <IconPlayerPauseFilled size={18} /> : <IconPlayerPlayFilled size={18} />}
      </ActionIcon>
      <Slider
        flex={1}
        min={0}
        max={max}
        value={Math.min(frame, max)}
        onChange={(value) => {
          setPlaying(false);
          onFrame(value);
        }}
        label={null}
        color="violet"
        marks={[
          { value: start, label: 'in' },
          { value: end, label: 'out' },
        ]}
        styles={
          compact
            ? {
                markLabel: {
                  color: 'rgba(255,255,255,0.9)',
                  textShadow: '0 1px 3px rgba(0,0,0,0.8)',
                },
              }
            : undefined
        }
      />
      <Text
        className="rc-tnum"
        fz="0.72rem"
        c={compact ? 'gray.0' : 'dark.2'}
        ta="right"
        style={{
          flex: 'none',
          // Worst-case width (tabular digits): stops the slider breathing as the
          // frame counter gains digits during playback.
          minWidth: `${`${max} / ${max}`.length}ch`,
          textShadow: compact ? '0 1px 3px rgba(0,0,0,0.8)' : undefined,
        }}
      >
        {frame} / {max}
      </Text>
    </>
  );

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#000',
          borderRadius: 'var(--mantine-radius-md)',
          overflow: 'hidden',
        }}
      >
        <video
          ref={video}
          src={
            version ? `${intrinsicPreviewUrl(camera)}?v=${version}` : intrinsicPreviewUrl(camera)
          }
          muted
          playsInline
          preload="auto"
          onLoadedMetadata={(event) => {
            event.currentTarget.currentTime = frameTime(Math.min(frame, max), fps);
          }}
          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
        />
        {compact && (
          <Group
            gap="sm"
            wrap="nowrap"
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              bottom: 0,
              padding: '10px 12px 14px',
              // Scrim so the controls read over any footage; fades to keep the frame visible.
              background: 'linear-gradient(transparent, rgba(0,0,0,0.72))',
            }}
          >
            {controls}
          </Group>
        )}
      </Box>
      {!compact && (
        <Group mt="sm" gap="sm" wrap="nowrap">
          {controls}
        </Group>
      )}
    </Box>
  );
}
