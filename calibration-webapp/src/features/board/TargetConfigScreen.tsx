import {
  Alert,
  Box,
  Button,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  Switch,
  Text,
} from '@mantine/core';
import { IconDownload, IconInfoCircle, IconRuler } from '@tabler/icons-react';
import { type ReactNode, useEffect, useRef, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { StickyActionBar } from '@/components/layout/StickyActionBar';
import {
  captureGridColumns,
  screenHeight,
  useCompactLayout,
} from '@/components/layout/useCompactLayout';
import { ScreenHeader } from '@/components/ScreenHeader';
import { selectDefaults } from '@/features/session/defaultsSlice';
import { applyBoardConfig, selectSession } from '@/features/session/sessionSlice';
import { fetchBoardDictionaries, previewBoard } from '@/transport/httpClient';
import type { Board, BoardTarget, BoardType } from '@/transport/types';

// For ChArUco the operator sets the square (measured, metric scale) + a marker ratio;
// the marker's mm size is derived from them. ArUco (single marker) is left as-is.
function normalizeBoard(board: Board): Board {
  if (board.board_type !== 'charuco') {
    return board;
  }
  return { ...board, marker_size_mm: board.marker_ratio * board.square_size_mm };
}

// Marker capacity from a predefined dictionary name (mirrors the backend): the
// trailing number, e.g. DICT_5X5_100 -> 100; DICT_ARUCO_ORIGINAL -> 1024.
function dictionaryCapacity(name: string): number {
  if (name === 'DICT_ARUCO_ORIGINAL') {
    return 1024;
  }
  const tail = Number(name.split('_').at(-1));
  return Number.isFinite(tail) ? tail : 50;
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb={11} style={{ letterSpacing: '0.07em' }}>
      {children}
    </Text>
  );
}

function FieldLabel({ children }: { children: ReactNode }) {
  return (
    <Text fz="0.69rem" c="dark.2" mb={6}>
      {children}
    </Text>
  );
}

const INPUT_STYLES = {
  input: {
    background: 'var(--rc-input)',
    borderColor: 'var(--mantine-color-dark-4)',
    fontVariantNumeric: 'tabular-nums' as const,
  },
} as const;

// Board seeds come from the persisted session boards, else the backend-served
// defaults (GET /defaults, ADR-0036) — no hardcoded board copy in the webapp.
// The form only mounts once a seed exists (defaults load at app mount, so the
// null render is a transient frame at worst).
export function TargetConfigScreen() {
  const session = useAppSelector(selectSession);
  const defaults = useAppSelector(selectDefaults);
  const intrinsicSeed = session?.intrinsic_board ?? defaults?.board ?? null;
  if (!intrinsicSeed) {
    return null;
  }
  return (
    <TargetConfigForm
      intrinsicSeed={intrinsicSeed}
      extrinsicSeed={session?.extrinsic_board ?? intrinsicSeed}
    />
  );
}

function TargetConfigForm({
  intrinsicSeed,
  extrinsicSeed,
}: {
  intrinsicSeed: Board;
  extrinsicSeed: Board;
}) {
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  const compact = useCompactLayout();

  const [dictionaries, setDictionaries] = useState<string[]>([intrinsicSeed.dictionary]);
  const [active, setActive] = useState<BoardTarget>(
    session?.step === 'extrinsic_board_choice' ? 'extrinsic' : 'intrinsic',
  );
  const [intrinsic, setIntrinsic] = useState<Board>(intrinsicSeed);
  const [extrinsic, setExtrinsic] = useState<Board>(extrinsicSeed);
  const [extrinsicDifferent, setExtrinsicDifferent] = useState<boolean>(
    session?.extrinsic_board != null,
  );
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const urlRef = useRef<string | null>(null);

  const editingInherited = active === 'extrinsic' && !extrinsicDifferent;
  const board = active === 'intrinsic' ? intrinsic : extrinsic;
  const setBoard = active === 'intrinsic' ? setIntrinsic : setExtrinsic;
  const patch = (fields: Partial<Board>) => setBoard((b) => ({ ...b, ...fields }));

  useEffect(() => {
    fetchBoardDictionaries()
      .then(setDictionaries)
      .catch(() => setDictionaries([intrinsicSeed.dictionary]));
  }, [intrinsicSeed.dictionary]);

  // Live preview: same render engine as the download (backend), debounced.
  const previewBoardValue = editingInherited ? intrinsic : board;
  const previewKey = JSON.stringify(normalizeBoard(previewBoardValue));
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      previewBoard(normalizeBoard(previewBoardValue))
        .then((blob) => {
          if (cancelled) return;
          const url = URL.createObjectURL(blob);
          if (urlRef.current) URL.revokeObjectURL(urlRef.current);
          urlRef.current = url;
          setPreviewUrl(url);
          setPreviewError(null);
        })
        .catch((err: unknown) => {
          if (!cancelled) setPreviewError(err instanceof Error ? err.message : 'preview failed');
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

  useEffect(() => () => void (urlRef.current && URL.revokeObjectURL(urlRef.current)), []);

  const save = async () => {
    const target: BoardTarget = active;
    setSaving(true);
    try {
      // Inheriting (extrinsic tab, box unchecked) sends board=null: the backend keeps
      // extrinsic_board null and completes Target Config.
      const payload = editingInherited ? null : normalizeBoard(board);
      await dispatch(applyBoardConfig({ target, board: payload })).unwrap();
      // Saving the intrinsic board advances to the extrinsic choice — surface that tab
      // (the backend now stops at extrinsic_board_choice, so the view stays here).
      if (target === 'intrinsic') {
        setActive('extrinsic');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <Box
      p={{ base: 'md', sm: 'xl' }}
      h={screenHeight(compact)}
      style={{ display: 'flex', flexDirection: 'column' }}
    >
      <ScreenHeader
        title="Target Config"
        subtitle="Define the ChArUco/ArUco board, download the PNG to print, then measure a printed square and enter its real size — that measurement is the metric scale."
      />

      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: captureGridColumns(compact),
          gap: 22,
        }}
      >
        {/* Left — preview + download (fills the column height like Camera Setup) */}
        <Box
          style={{
            minWidth: 0,
            minHeight: 0,
            border: '1px solid var(--rc-border)',
            borderRadius: 'var(--mantine-radius-lg)',
            background: 'var(--rc-panel)',
            padding: 16,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
          }}
        >
          <Box
            style={{
              flex: 1,
              minHeight: 'min(48vh, 520px)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: '#ffffff',
              borderRadius: 'var(--mantine-radius-md)',
              overflow: 'hidden',
              padding: 12,
            }}
          >
            {previewError ? (
              <Text c="var(--rc-error)" fz="0.82rem" p="md" ta="center">
                {previewError}
              </Text>
            ) : previewUrl ? (
              <img
                src={previewUrl}
                alt="Board preview"
                style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
              />
            ) : (
              <Text c="dark.4" fz="0.82rem">
                Rendering…
              </Text>
            )}
          </Box>

          <Group justify="space-between" wrap="wrap" gap="sm">
            <Button
              component="a"
              href={previewUrl ?? undefined}
              download={`board_${active}.png`}
              disabled={!previewUrl}
              leftSection={<IconDownload size={16} />}
            >
              Download PNG
            </Button>
          </Group>

          <Alert
            variant="light"
            color="yellow"
            icon={<IconRuler size={16} />}
            styles={{ message: { fontSize: '0.78rem', lineHeight: 1.5 } }}
          >
            Print the PNG, then measure a printed square with a caliper and enter its real size below.
            The measurement — not the print scale — sets the metric scale.
          </Alert>
        </Box>

        {/* Right — settings */}
        <Box
          style={{
            minHeight: 0,
            // Flow: the page scrolls, so the sticky Save bar sticks to the viewport;
            // an internal scroll here would trap it in a non-scrolling box (ADR-0041).
            overflowY: compact ? 'visible' : 'auto',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <SegmentedControl
            fullWidth
            color="violet"
            size="md"
            value={active}
            onChange={(v) => setActive(v as BoardTarget)}
            data={[
              { label: 'Intrinsic board', value: 'intrinsic' },
              { label: 'Extrinsic board', value: 'extrinsic' },
            ]}
            styles={{ label: { fontWeight: 600 } }}
            mb="md"
          />

          {active === 'extrinsic' && (
            <Switch
              checked={extrinsicDifferent}
              onChange={(e) => setExtrinsicDifferent(e.currentTarget.checked)}
              label="Use a different board for extrinsic"
              mb="md"
            />
          )}

          {editingInherited ? (
            <Alert variant="light" color="gray" icon={<IconInfoCircle size={16} />}>
              <Text fz="0.82rem">The extrinsic calibration inherits the intrinsic board.</Text>
            </Alert>
          ) : (
            <Box
              style={{
                border: '1px solid var(--rc-border)',
                borderRadius: 'var(--mantine-radius-lg)',
                background: 'var(--rc-panel)',
                padding: 16,
              }}
            >
              <SectionLabel>Board</SectionLabel>
              <SegmentedControl
                fullWidth
                value={board.board_type}
                onChange={(v) => patch({ board_type: v as BoardType })}
                // Single ArUco markers are extrinsic-only (the backend also
                // rejects them at POST /board): the intrinsic tab simply does
                // not offer the option — a disabled segment read as a bug.
                data={
                  active === 'intrinsic'
                    ? [{ label: 'ChArUco', value: 'charuco' }]
                    : [
                        { label: 'ChArUco', value: 'charuco' },
                        { label: 'ArUco', value: 'aruco' },
                      ]
                }
                mb="md"
              />

              <FieldLabel>Dictionary</FieldLabel>
              <Select
                value={board.dictionary}
                onChange={(v) => v && patch({ dictionary: v })}
                data={dictionaries}
                allowDeselect={false}
                comboboxProps={{ withinPortal: true }}
                styles={INPUT_STYLES}
                mb={6}
              />
              <Text fz="0.68rem" c="dark.3" mb="md" style={{ lineHeight: 1.5 }}>
                <Text span fw={600} inherit>
                  NxN
                </Text>{' '}
                = marker bit grid: 4×4 reads from farther / lower resolution, 7×7 is more robust but
                needs more pixels.
              </Text>

              {board.board_type === 'charuco' ? (
                <>
                  <Group grow mb="md">
                    <Box>
                      <FieldLabel>Columns</FieldLabel>
                      <NumberInput
                        value={board.columns}
                        onChange={(v) => patch({ columns: Number(v) || 0 })}
                        min={2}
                        max={30}
                        styles={INPUT_STYLES}
                      />
                    </Box>
                    <Box>
                      <FieldLabel>Rows</FieldLabel>
                      <NumberInput
                        value={board.rows}
                        onChange={(v) => patch({ rows: Number(v) || 0 })}
                        min={2}
                        max={30}
                        styles={INPUT_STYLES}
                      />
                    </Box>
                  </Group>

                  <Group grow mb={6}>
                    <Box>
                      <FieldLabel>Square size (mm, measured)</FieldLabel>
                      <NumberInput
                        value={board.square_size_mm}
                        onChange={(v) => patch({ square_size_mm: Number(v) || 0 })}
                        min={1}
                        decimalScale={2}
                        step={0.5}
                        styles={INPUT_STYLES}
                      />
                    </Box>
                    <Box>
                      <FieldLabel>Marker ratio</FieldLabel>
                      <NumberInput
                        value={board.marker_ratio}
                        onChange={(v) => patch({ marker_ratio: Number(v) || 0 })}
                        min={0.1}
                        max={0.95}
                        decimalScale={2}
                        step={0.05}
                        styles={INPUT_STYLES}
                      />
                    </Box>
                  </Group>
                  <Text fz="0.68rem" c="dark.3" mb="md" style={{ lineHeight: 1.5 }}>
                    <Text span fw={600} inherit>
                      Square
                    </Text>{' '}
                    = checkerboard cell — the metric scale you measure. The ArUco marker printed
                    inside each white cell is that ratio of it (≈ 0.75).
                  </Text>
                </>
              ) : (
                <Group grow mb="md">
                  <Box>
                    <FieldLabel>Marker ID</FieldLabel>
                    <NumberInput
                      value={board.marker_id}
                      onChange={(v) => patch({ marker_id: Number(v) || 0 })}
                      min={0}
                      max={dictionaryCapacity(board.dictionary) - 1}
                      styles={INPUT_STYLES}
                    />
                  </Box>
                  <Box>
                    <FieldLabel>Marker size (mm, measured)</FieldLabel>
                    <NumberInput
                      value={board.marker_size_mm}
                      onChange={(v) => patch({ marker_size_mm: Number(v) || 0 })}
                      min={1}
                      decimalScale={2}
                      step={0.5}
                      styles={INPUT_STYLES}
                    />
                  </Box>
                </Group>
              )}

              <Switch
                checked={board.inverted}
                onChange={(e) => patch({ inverted: e.currentTarget.checked })}
                label="Inverted (ink saving)"
              />
            </Box>
          )}

          {/* Always present — the extrinsic choice (a board, or inherit) must be
              confirmed to complete Target Config, so it can't be skipped. */}
          <StickyActionBar>
            <Button fullWidth mt="lg" onClick={save} loading={saving}>
              Save {active} board
            </Button>
          </StickyActionBar>
        </Box>
      </Box>
    </Box>
  );
}
