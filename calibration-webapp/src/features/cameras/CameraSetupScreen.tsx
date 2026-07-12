import {
  closestCenter,
  DndContext,
  type DragEndEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import { restrictToParentElement, restrictToVerticalAxis } from '@dnd-kit/modifiers';
import {
  arrayMove,
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Box, Button, Group, Select, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconGripVertical, IconInfoCircle, IconRefresh } from '@tabler/icons-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { ScreenHeader } from '@/components/ScreenHeader';
import {
  buildConfigRequest,
  commonResolutions,
  defaultCapture,
  offeredFps,
  outputDimensions,
  parseResolution,
  RESIZE_FACTORS,
} from '@/features/cameras/captureOptions';
import {
  detectCamerasThunk,
  selectDetectedCameras,
  selectDetectStatus,
} from '@/features/cameras/camerasSlice';
import { PreviewGrid, type TrackArrangement } from '@/features/preview/PreviewGrid';
import {
  applyCameraConfig,
  confirmCameraSetupThunk,
  rehydrateSession,
  reorderCamerasThunk,
  selectSession,
} from '@/features/session/sessionSlice';
import { errorMessage, intrinsicPreviewUrl } from '@/transport/httpClient';
import type { Session } from '@/transport/types';

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <Text
      fz="0.66rem"
      fw={600}
      c="dark.3"
      tt="uppercase"
      mb={11}
      style={{ letterSpacing: '0.07em' }}
    >
      {children}
    </Text>
  );
}

const SELECT_STYLES = {
  input: {
    background: 'var(--rc-input)',
    borderColor: 'var(--mantine-color-dark-4)',
    fontVariantNumeric: 'tabular-nums' as const,
  },
} as const;

function FieldLabel({ children }: { children: ReactNode }) {
  return (
    <Text fz="0.69rem" c="dark.2" mb={6}>
      {children}
    </Text>
  );
}

interface RowData {
  index: number;
  name: string;
  devicePath: string;
}

function CameraRow({ row }: { row: RowData }) {
  const anchor = row.index === 0;
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: row.devicePath,
  });

  return (
    <Box
      ref={setNodeRef}
      p={10}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        borderRadius: 'var(--mantine-radius-lg)',
        border: `1px solid ${isDragging ? 'rgba(167,139,250,0.45)' : 'var(--mantine-color-dark-4)'}`,
        background: 'var(--rc-panel)',
        transform: CSS.Transform.toString(transform),
        transition,
        zIndex: isDragging ? 1 : undefined,
        boxShadow: isDragging ? '0 8px 24px rgba(0,0,0,0.45)' : undefined,
      }}
    >
      <Box
        component="button"
        type="button"
        {...attributes}
        {...listeners}
        aria-label="Drag to reorder"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 28,
          height: 28,
          flex: 'none',
          border: 'none',
          background: 'transparent',
          color: 'var(--mantine-color-dark-3)',
          cursor: 'grab',
          touchAction: 'none',
        }}
      >
        <IconGripVertical size={15} />
      </Box>
      <Box
        style={{
          width: 26,
          height: 26,
          flex: 'none',
          borderRadius: 7,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: 'var(--mantine-font-family-headings)',
          fontWeight: 600,
          fontSize: '0.78rem',
          background: anchor ? 'rgba(167,139,250,0.14)' : 'var(--rc-input)',
          color: anchor ? 'var(--rc-accent-bright)' : 'var(--mantine-color-dark-2)',
          border: `1px solid ${anchor ? 'rgba(167,139,250,0.35)' : 'var(--mantine-color-dark-4)'}`,
        }}
      >
        {row.index}
      </Box>
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Group gap={7} wrap="nowrap">
          <Text fw={600} fz="0.81rem">
            {row.name}
          </Text>
          {anchor && (
            <Text fz="0.625rem" c="var(--rc-accent-bright)" style={{ whiteSpace: 'nowrap' }}>
              ★ anchor
            </Text>
          )}
        </Group>
        <Text
          fz="0.625rem"
          c="dark.3"
          className="rc-tnum"
          style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
        >
          {row.devicePath}
        </Text>
      </Box>
    </Box>
  );
}

// First-frame thumbnail of an imported camera's recording, via the preview mp4
// (ADR-0027) seeked to 0. Fills its grid cell and letterboxes the frame
// (objectFit: contain) exactly like the live CameraTile. The transcode is kicked
// at import time; until it is ready the <video> 404s and we fall back to a
// quiet placeholder.
function ImportThumbnail({ name }: { name: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <Box
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        borderRadius: 'var(--mantine-radius-lg)',
        border: '1px solid var(--mantine-color-dark-4)',
        background: '#000',
        overflow: 'hidden',
      }}
    >
      {failed ? (
        <Box
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text c="dark.3" fz="0.75rem">
            preview preparing…
          </Text>
        </Box>
      ) : (
        <video
          src={intrinsicPreviewUrl(name)}
          preload="metadata"
          muted
          playsInline
          onError={() => setFailed(true)}
          style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
        />
      )}
      <Text
        fw={600}
        fz="0.72rem"
        px={8}
        py={3}
        style={{
          position: 'absolute',
          left: 8,
          bottom: 8,
          borderRadius: 7,
          background: 'rgba(11,11,14,0.72)',
          border: '1px solid var(--mantine-color-dark-4)',
        }}
      >
        {name}
      </Text>
    </Box>
  );
}

// Read-only Camera Setup for an imported session (ADR-0031): the cameras derive
// from the uploaded videos — nothing to detect, reordering disabled (the camera
// number in the file name fixes the order, cam_0 = anchor). Continue advances the
// wizard without rebuilding the configs (/cameras/confirm).
function ImportedCameraSetup({ session }: { session: Session }) {
  const dispatch = useAppDispatch();
  // Same responsive switch as the live CameraGrid: desktop fills the area with a
  // near-square grid (no scroll); phone/portrait scrolls a single column.
  const compact = useMediaQuery('(max-width: 47.99em), (orientation: portrait)') ?? false;
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cameras = [...session.cameras].sort((a, b) => a.index - b.index);
  const cols = compact ? 1 : Math.ceil(Math.sqrt(Math.max(1, cameras.length)));
  const rows = Math.ceil(Math.max(1, cameras.length) / cols);

  const confirm = async () => {
    setConfirming(true);
    setError(null);
    try {
      await dispatch(confirmCameraSetupThunk()).unwrap();
      // The persisted step moves to intrinsic_capture; the rail follows it.
    } catch (err) {
      setError(errorMessage(err, 'could not confirm the setup'));
    } finally {
      setConfirming(false);
    }
  };

  return (
    <Box p={{ base: 'md', sm: 'xl' }} h="100%" style={{ display: 'flex', flexDirection: 'column' }}>
      <ScreenHeader
        title="Camera Setup"
        subtitle={
          <>
            Imported session — the cameras derive from the uploaded videos.{' '}
            <Text span c="var(--rc-accent-bright)" inherit>
              Index 0 (cam_0) is the extrinsic anchor.
            </Text>
          </>
        }
      />

      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 348px',
          gap: 24,
        }}
        className="rc-camsetup-grid"
      >
        <Box style={{ minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <SectionLabel>Recordings · first frame</SectionLabel>
          {/* Mirrors the live PreviewGrid geometry: fills the column height with a
              cols = ceil(sqrt(n)) letterboxed grid (no scroll); same 58vh floor. */}
          <Box style={{ flex: 1, minHeight: 'min(58vh, 560px)' }}>
            <Box
              style={
                compact
                  ? {
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 12,
                      height: '100%',
                      overflowY: 'auto',
                    }
                  : {
                      display: 'grid',
                      gridTemplateColumns: `repeat(${cols}, 1fr)`,
                      gridTemplateRows: `repeat(${rows}, 1fr)`,
                      gap: 12,
                      height: '100%',
                      overflow: 'hidden',
                    }
              }
            >
              {cameras.map((camera) => (
                <Box
                  key={camera.name}
                  style={
                    compact
                      ? { width: '100%', aspectRatio: '16 / 9', flex: '0 0 auto' }
                      : { minWidth: 0, minHeight: 0 }
                  }
                >
                  <ImportThumbnail name={camera.name} />
                </Box>
              ))}
            </Box>
          </Box>
        </Box>

        <Box style={{ display: 'flex', flexDirection: 'column', gap: 18, minWidth: 0 }}>
          <Box>
            <SectionLabel>
              Cameras{' '}
              <Text span c="dark.3" tt="none" style={{ letterSpacing: 0 }} inherit>
                · order fixed by the file numbering
              </Text>
            </SectionLabel>
            <Box style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {cameras.map((camera) => (
                <Box
                  key={camera.name}
                  p={10}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    borderRadius: 'var(--mantine-radius-lg)',
                    border: '1px solid var(--mantine-color-dark-4)',
                    background: 'var(--rc-panel)',
                  }}
                >
                  <Box
                    style={{
                      width: 26,
                      height: 26,
                      flex: 'none',
                      borderRadius: 7,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontFamily: 'var(--mantine-font-family-headings)',
                      fontWeight: 600,
                      fontSize: '0.78rem',
                      background: camera.index === 0 ? 'rgba(167,139,250,0.14)' : 'var(--rc-input)',
                      color:
                        camera.index === 0
                          ? 'var(--rc-accent-bright)'
                          : 'var(--mantine-color-dark-2)',
                      border: `1px solid ${
                        camera.index === 0
                          ? 'rgba(167,139,250,0.35)'
                          : 'var(--mantine-color-dark-4)'
                      }`,
                    }}
                  >
                    {camera.index}
                  </Box>
                  <Box style={{ flex: 1, minWidth: 0 }}>
                    <Group gap={7} wrap="nowrap">
                      <Text fw={600} fz="0.81rem">
                        {camera.name}
                      </Text>
                      {camera.index === 0 && (
                        <Text
                          fz="0.625rem"
                          c="var(--rc-accent-bright)"
                          style={{ whiteSpace: 'nowrap' }}
                        >
                          ★ anchor
                        </Text>
                      )}
                    </Group>
                    <Text
                      fz="0.625rem"
                      c="dark.3"
                      className="rc-tnum"
                      style={{ whiteSpace: 'nowrap' }}
                    >
                      {camera.width}×{camera.height} · {camera.fps} fps
                    </Text>
                  </Box>
                </Box>
              ))}
            </Box>
          </Box>

          <Box
            p={16}
            style={{
              borderRadius: 'var(--mantine-radius-xl)',
              border: '1px solid var(--mantine-color-dark-4)',
              background: '#101014',
            }}
          >
            <SectionLabel>Imported configuration</SectionLabel>
            <Text fz="0.78rem" c="dark.2" style={{ lineHeight: 1.55 }}>
              Resolution and frame rate were read from the videos; capture and reordering are
              disabled for imported sessions.
            </Text>
            {error && (
              <Text fz="0.78rem" c="var(--rc-error)" mt={10}>
                {error}
              </Text>
            )}
            <Button
              color="violet"
              fullWidth
              mt={13}
              loading={confirming}
              onClick={() => void confirm()}
            >
              Continue to Intrinsics
            </Button>
          </Box>
        </Box>
      </Box>
    </Box>
  );
}

// Camera Setup: routes on the session mode (ADR-0031) — an imported session gets
// the read-only variant (thumbnails, fixed order); a realtime one the live flow.
export function CameraSetupScreen() {
  const session = useAppSelector(selectSession);
  if (session?.mode === 'load-from-files') {
    return <ImportedCameraSetup session={session} />;
  }
  return <LiveCameraSetup />;
}

// Live Camera Setup: preview (left) + the shared capture configuration and index order
// (right). The capture format is uniform across the array (spec camera-detection-config):
// resolution / fps are intersected over all detected cameras, the resize factor derives
// an even output size, and Apply persists the config + republishes (cascade).
function LiveCameraSetup() {
  const dispatch = useAppDispatch();
  const detected = useAppSelector(selectDetectedCameras);
  const detectStatus = useAppSelector(selectDetectStatus);
  const session = useAppSelector(selectSession);

  const [prefix, setPrefix] = useState('cam');
  const [resolution, setResolution] = useState<string | null>(null);
  const [resizeFactor, setResizeFactor] = useState<number>(1);
  const [fps, setFps] = useState<number | null>(null);
  const [order, setOrder] = useState<string[]>([]);
  const [applying, setApplying] = useState(false);
  // Debounced reorder persistence: a multi-move sort must trigger ONE server
  // refresh (each one republishes the whole publisher session). Flushed on
  // unmount so a quick drag-then-navigate is never lost.
  const reorderTimer = useRef<number | undefined>(undefined);
  const pendingReorder = useRef<string[] | null>(null);

  useEffect(
    () => () => {
      window.clearTimeout(reorderTimer.current);
      if (pendingReorder.current) {
        void dispatch(reorderCamerasThunk(pendingReorder.current));
        pendingReorder.current = null;
      }
    },
    [dispatch],
  );

  // A small drag distance avoids hijacking taps/clicks (touch + mouse).
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  // Detect once on first mount (then on demand via Re-detect).
  useEffect(() => {
    if (detectStatus === 'idle') {
      dispatch(detectCamerasThunk());
    }
  }, [detectStatus, dispatch]);

  // Seed the controls from an existing config if present, else from detection defaults.
  useEffect(() => {
    if (detected.length === 0) {
      return;
    }
    const first = session?.cameras[0];
    if (first) {
      setResolution(`${first.width}x${first.height}`);
      setResizeFactor(first.resize_factor);
      setFps(first.fps);
      setPrefix(first.prefix);
      return;
    }
    const fallback = defaultCapture(detected);
    if (fallback) {
      setResolution(fallback.resolution.value);
      setFps(fallback.fps);
    }
  }, [detected, session]);

  // Index order: from the persisted config (by index) if present, else detection order.
  useEffect(() => {
    const configured = session?.cameras ?? [];
    if (configured.length > 0) {
      setOrder([...configured].sort((a, b) => a.index - b.index).map((c) => c.device_path));
    } else {
      setOrder(detected.map((d) => d.device_path));
    }
  }, [detected, session]);

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) {
      return;
    }
    const next = arrayMove(order, order.indexOf(String(active.id)), order.indexOf(String(over.id)));
    setOrder(next);
    // Persist the reorder when these devices are already configured (index =
    // position, calibrations kept server-side — /cameras/order), DEBOUNCED so a
    // sort in several moves fires one refresh. Before the first Apply there is
    // nothing to persist: the order ships with Apply.
    const configured = session?.cameras ?? [];
    if (
      configured.length > 0 &&
      configured.length === next.length &&
      configured.every((camera) => next.includes(camera.device_path))
    ) {
      pendingReorder.current = next;
      window.clearTimeout(reorderTimer.current);
      reorderTimer.current = window.setTimeout(() => {
        pendingReorder.current = null;
        // On rejection (e.g. the device set changed under us) resync local order
        // from the server rather than leaving the UI diverged.
        void dispatch(reorderCamerasThunk(next))
          .unwrap()
          .catch(() => dispatch(rehydrateSession()));
      }, 400);
    }
  };

  const resolutionOptions = commonResolutions(detected);
  const selected = resolution ? parseResolution(resolution) : null;
  const fpsOptions = selected ? offeredFps(detected, selected.width, selected.height) : [];
  const output = selected ? outputDimensions(selected.width, selected.height, resizeFactor) : null;

  const onResolutionChange = (value: string | null) => {
    setResolution(value);
    if (value) {
      const { width, height } = parseResolution(value);
      const next = offeredFps(detected, width, height);
      setFps(next[0] ?? null);
    }
  };

  // Detected cameras in the operator's chosen order — the source for both the list
  // and the (uniform) config request (index = position, so index 0 = anchor).
  const orderedCameras = order
    .map((path) => detected.find((d) => d.device_path === path))
    .filter((d): d is NonNullable<typeof d> => d !== undefined);

  const onApply = async () => {
    if (!resolution || fps === null) {
      return;
    }
    const { width, height } = parseResolution(resolution);
    setApplying(true);
    try {
      await dispatch(
        applyCameraConfig(
          buildConfigRequest(orderedCameras, { prefix, width, height, resizeFactor, fps }),
        ),
      ).unwrap();
    } catch {
      // Error surfaced via session slice; keep the form usable.
    } finally {
      setApplying(false);
    }
  };

  const configured = session?.cameras ?? [];
  // Position-based name: a reorder card previews what the camera becomes after Apply
  // (cam_<new index>), matching the live tile relabel — not the persisted (device-
  // bound) name, which would not move with the drag.
  const rows: RowData[] = order.map((path, index) => ({
    index,
    name: `${prefix}_${index}`,
    devicePath: path,
  }));

  // Reflect the pending reorder in the live preview: map each track to its device
  // (by configured name, else by detection index `cam_<i>`), then to its position in
  // the operator's order. The tile moves + relabels on drop, before any republish.
  const arrange: TrackArrangement = (name) => {
    const byConfig = configured.find((c) => c.name === name);
    const device = byConfig
      ? byConfig.device_path
      : detected.find((d) => `cam_${d.index}` === name)?.device_path;
    if (!device) {
      return null;
    }
    const position = order.indexOf(device);
    if (position < 0) {
      return null;
    }
    return { sortIndex: position, label: `${prefix}_${position}` };
  };

  const detecting = detectStatus === 'loading';
  const noCommon = detected.length > 0 && resolutionOptions.length === 0;

  return (
    <Box p={{ base: 'md', sm: 'xl' }} h="100%" style={{ display: 'flex', flexDirection: 'column' }}>
      <ScreenHeader
        title="Camera Setup"
        subtitle={
          <>
            Detect USB cameras, set the shared capture format, and order the indices.{' '}
            <Text span c="var(--rc-accent-bright)" inherit>
              Index 0 is the extrinsic anchor.
            </Text>
          </>
        }
        right={
          <Group gap={9} wrap="nowrap">
            <Box
              h={38}
              px={13}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                borderRadius: 'var(--mantine-radius-sm)',
                border: '1px solid var(--mantine-color-dark-4)',
                background: 'var(--rc-input)',
              }}
            >
              <Text fz="0.78rem" c="dark.2">
                prefix
              </Text>
              <Text fz="0.78rem" fw={600}>
                {prefix}
              </Text>
            </Box>
            <Button
              variant="light"
              color="violet"
              h={38}
              leftSection={<IconRefresh size={15} />}
              loading={detecting}
              onClick={() => dispatch(detectCamerasThunk())}
            >
              Re-detect
            </Button>
          </Group>
        }
      />

      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 348px',
          gap: 24,
        }}
        className="rc-camsetup-grid"
      >
        <Box style={{ minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <SectionLabel>Preview · map physical ↔ index</SectionLabel>
          {/* Fills the column height; the tile grid letterboxes inside with no scroll.
              minHeight floors it so the stacked mobile layout keeps a usable preview. */}
          <Box style={{ flex: 1, minHeight: 'min(58vh, 560px)' }}>
            <PreviewGrid arrange={arrange} />
          </Box>
        </Box>

        <Box
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 18,
            minWidth: 0,
            minHeight: 0,
            overflowY: 'auto',
          }}
        >
          <Box>
            <SectionLabel>
              Reorder cameras{' '}
              <Text span c="dark.3" tt="none" style={{ letterSpacing: 0 }} inherit>
                · drag · index 0 = anchor
              </Text>
            </SectionLabel>
            {rows.length > 0 ? (
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                modifiers={[restrictToVerticalAxis, restrictToParentElement]}
                onDragEnd={onDragEnd}
              >
                <SortableContext items={order} strategy={verticalListSortingStrategy}>
                  <Box style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {rows.map((row) => (
                      <CameraRow key={row.devicePath} row={row} />
                    ))}
                  </Box>
                </SortableContext>
              </DndContext>
            ) : (
              <Text c="dark.3" fz="0.81rem">
                {detecting ? 'Detecting cameras…' : 'No cameras detected.'}
              </Text>
            )}
          </Box>
          <Box
            p={16}
            style={{
              borderRadius: 'var(--mantine-radius-xl)',
              border: '1px solid var(--mantine-color-dark-4)',
              background: '#101014',
            }}
          >
            <SectionLabel>
              Capture configuration{' '}
              <Text span c="dark.3" tt="none" style={{ letterSpacing: 0 }} inherit>
                · all cameras
              </Text>
            </SectionLabel>

            {noCommon ? (
              <Text fz="0.78rem" c="var(--rc-warning)">
                No resolution is supported by all detected cameras. Remove the incompatible camera
                or re-detect.
              </Text>
            ) : (
              <Box style={{ display: 'flex', flexDirection: 'column', gap: 13 }}>
                <Box>
                  <FieldLabel>
                    Resolution{' '}
                    <Text span c="dark.3" inherit>
                      · V4L2 native
                    </Text>
                  </FieldLabel>
                  <Select
                    data={resolutionOptions.map((r) => ({
                      value: r.value,
                      label: `${r.width}×${r.height}`,
                    }))}
                    value={resolution}
                    onChange={onResolutionChange}
                    allowDeselect={false}
                    disabled={detecting || resolutionOptions.length === 0}
                    placeholder={detecting ? 'Detecting…' : 'Select resolution'}
                    comboboxProps={{ withinPortal: true }}
                    styles={SELECT_STYLES}
                  />
                </Box>

                <Box>
                  <Group justify="space-between" align="baseline" mb={6}>
                    <Text fz="0.69rem" c="dark.2">
                      Resize factor · s
                    </Text>
                    {output && (
                      <Text fz="0.69rem" c="var(--rc-accent)" className="rc-tnum">
                        output → {output.width}×{output.height}
                      </Text>
                    )}
                  </Group>
                  <Select
                    data={RESIZE_FACTORS.map((s) => ({
                      value: String(s),
                      label: `s = ${s.toFixed(2)}`,
                    }))}
                    value={String(resizeFactor)}
                    onChange={(value) => value && setResizeFactor(Number(value))}
                    allowDeselect={false}
                    disabled={detecting}
                    comboboxProps={{ withinPortal: true }}
                    styles={SELECT_STYLES}
                  />
                </Box>

                <Box>
                  <FieldLabel>Capture FPS</FieldLabel>
                  <Select
                    data={fpsOptions.map((f) => ({ value: String(f), label: `${f} fps` }))}
                    value={fps !== null ? String(fps) : null}
                    onChange={(value) => value && setFps(Number(value))}
                    allowDeselect={false}
                    disabled={detecting || fpsOptions.length === 0}
                    placeholder={detecting ? 'Detecting…' : 'Select fps'}
                    comboboxProps={{ withinPortal: true }}
                    styles={SELECT_STYLES}
                  />
                  {fpsOptions.length > 0 && (
                    <Text fz="0.625rem" c="dark.3" mt={6} style={{ lineHeight: 1.5 }}>
                      Rates at or below the camera's native max for this resolution. Lower rates are
                      paced by the service (fewer frames, less USB load).
                    </Text>
                  )}
                </Box>

                <Button
                  color="violet"
                  fullWidth
                  mt={2}
                  loading={applying}
                  disabled={!resolution || fps === null}
                  onClick={onApply}
                >
                  Apply configuration
                </Button>
              </Box>
            )}

            <Group
              gap={7}
              mt={13}
              pt={13}
              wrap="nowrap"
              align="flex-start"
              style={{ borderTop: '1px solid var(--mantine-color-dark-4)' }}
            >
              <IconInfoCircle
                size={13}
                color="var(--rc-accent)"
                style={{ flex: 'none', marginTop: 1 }}
              />
              <Text fz="0.66rem" c="dark.3" style={{ lineHeight: 1.5 }}>
                Calibrated in native; the factor applies on export (K_out = s·K). Applying a new
                resolution invalidates all intrinsics (cascade).
              </Text>
            </Group>
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
