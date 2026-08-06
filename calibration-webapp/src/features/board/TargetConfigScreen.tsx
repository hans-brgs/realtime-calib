import {
  Alert,
  Box,
  Button,
  Group,
  NumberInput,
  Paper,
  SegmentedControl,
  Select,
  Switch,
  Text,
} from '@mantine/core';
import { IconDownload, IconInfoCircle, IconRuler } from '@tabler/icons-react';
import { type ReactNode, useEffect, useId, useRef, useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { HintedLabel } from '@/components/FieldHint';
import { StickyActionBar } from '@/components/layout/StickyActionBar';
import {
  captureGridColumns,
  screenHeight,
  useCompactLayout,
} from '@/components/layout/useCompactLayout';
import { ScreenHeader } from '@/components/ScreenHeader';
import { measurementOf, withMeasurement } from '@/features/board/measurement';
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

// Mantine's own label/error slots rather than hand-rolled <Text> siblings: they carry
// the aria wiring an input needs, and keep the text tied to its field when a Group
// reflows on narrow screens. No `description` slot left — the two texts worth keeping
// moved behind a `HintedLabel` icon, which reuses `label` from here so a hinted label
// and a plain one render identically.
const INPUT_STYLES = {
  input: {
    background: 'var(--rc-input)',
    borderColor: 'var(--mantine-color-dark-4)',
    fontVariantNumeric: 'tabular-nums' as const,
  },
  label: {
    fontSize: '0.69rem',
    fontWeight: 400,
    color: 'var(--mantine-color-dark-2)',
    marginBottom: 6,
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
      measurementSeed={measurementOf(session)}
    />
  );
}

function TargetConfigForm({
  intrinsicSeed,
  extrinsicSeed,
  measurementSeed,
}: {
  intrinsicSeed: Board;
  extrinsicSeed: Board;
  measurementSeed: number | '';
}) {
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  const compact = useCompactLayout();

  // The two hinted fields render their own <label>, so they need a stable id to point it at.
  const dictionaryId = useId();
  const markerRatioId = useId();

  const [dictionaries, setDictionaries] = useState<string[]>([intrinsicSeed.dictionary]);
  const [active, setActive] = useState<BoardTarget>(
    session?.step === 'extrinsic_board_choice' ? 'extrinsic' : 'intrinsic',
  );
  const [intrinsic, setIntrinsic] = useState<Board>(intrinsicSeed);
  const [extrinsic, setExtrinsic] = useState<Board>(extrinsicSeed);
  // Read from the stored intention, not from the data: an inherited board is
  // materialized (a copy of the intrinsic geometry), so its mere presence proves
  // nothing — and comparing geometries would call a deliberately identical
  // separate board "inherited".
  const [extrinsicDifferent, setExtrinsicDifferent] = useState<boolean>(
    session?.extrinsic_board != null && !session.extrinsic_inherited,
  );
  // One measurement for all three size fields (inherited board, separate ChArUco,
  // separate marker) — it is the same physical question each time. Held apart
  // from the board objects so the field can start empty without putting a zero
  // size on a board, and so clearing it to retype cannot corrupt one.
  const [measurement, setMeasurement] = useState<number | ''>(measurementSeed);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const urlRef = useRef<string | null>(null);

  const editingInherited = active === 'extrinsic' && !extrinsicDifferent;
  const board = active === 'intrinsic' ? intrinsic : extrinsic;
  const setBoard = active === 'intrinsic' ? setIntrinsic : setExtrinsic;
  const patch = (fields: Partial<Board>) => setBoard((b) => ({ ...b, ...fields }));

  // The printed size feeds the EXTRINSIC solve alone: the intrinsic solve runs
  // on unit squares (the backend builds the ChArUco board with
  // squareLength=1.0), so scaling the target leaves the camera matrix and the
  // distortion untouched. The measurement therefore lives on the extrinsic tab
  // only — asking for it while defining the intrinsic board implied it fed that
  // calibration.
  //
  // Inheriting is the case to watch: the extrinsic step then uses the intrinsic
  // board object itself, so the measurement taken here ends up on THAT board.
  const missingMeasurement =
    active === 'extrinsic' && !(typeof measurement === 'number' && measurement > 0);
  const measurementError = missingMeasurement
    ? 'Required — this measurement sets the extrinsic scale.'
    : undefined;
  // Mantine hands back '' for an emptied field; keep it empty rather than
  // coercing to 0, so "not measured yet" stays distinct from "measured zero".
  const onMeasurementChange = (value: number | string) => {
    const next = typeof value === 'number' ? value : Number(value);
    setMeasurement(value === '' || !Number.isFinite(next) ? '' : next);
  };

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
      // Guarded by the disabled Save, so on the extrinsic tab this is a number.
      const mm = typeof measurement === 'number' ? measurement : 0;
      // One button, one target, one write. Inheriting sends the intrinsic
      // geometry carrying the measurement taken here, flagged `inherited`: the
      // backend materializes it under [extrinsic_board], so config.toml tells the
      // same story as this screen. It rebuilds the geometry from the intrinsic
      // board itself, so the copy cannot drift from what it claims to inherit.
      // The intrinsic tab carries no measurement — that calibration is scale-free.
      const measured = editingInherited ? intrinsic : board;
      const payload = normalizeBoard(
        target === 'extrinsic' ? withMeasurement(measured, mm) : measured,
      );
      await dispatch(
        applyBoardConfig({ target, board: payload, inherited: editingInherited }),
      ).unwrap();
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
        <Paper
          radius="lg"
          withBorder
          p={16}
          style={{
            minWidth: 0,
            minHeight: 0,
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

          {/* Mirrors where the measurement is asked for: assertive on the
              extrinsic tab, silent about scale while the intrinsic board is
              being defined. */}
          <Alert
            variant="light"
            color={active === 'extrinsic' ? 'yellow' : 'gray'}
            icon={active === 'extrinsic' ? <IconRuler size={16} /> : <IconInfoCircle size={16} />}
            styles={{ message: { fontSize: '0.78rem', lineHeight: 1.5 } }}
          >
            {active === 'extrinsic'
              ? 'Print the PNG, then measure a printed square with a caliper and enter its real size below. The measurement — not the print scale — sets the metric scale.'
              : 'Print the PNG and calibrate the lenses with it. Its printed size is asked for at the extrinsic step: the intrinsic solve is scale-free.'}
          </Alert>
        </Paper>

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
            <>
              <Alert variant="light" color="gray" icon={<IconInfoCircle size={16} />} mb="md">
                <Text fz="0.82rem">The extrinsic calibration inherits the intrinsic board.</Text>
              </Alert>
              {/* The geometry is inherited, the measurement is not: it is asked
                  for here and stored here, on the materialized extrinsic board.
                  No description: the caliper instruction and what the measurement
                  buys are the subject of the Alert in the left column, and the
                  asterisk plus the empty-field error carry the requirement. */}
              <NumberInput
                label="Square size (mm)"
                withAsterisk
                error={measurementError}
                placeholder="measure the print"
                value={measurement}
                onChange={onMeasurementChange}
                min={1}
                decimalScale={2}
                step={0.5}
                styles={INPUT_STYLES}
              />
            </>
          ) : (
            <Paper radius="lg" withBorder p={16}>
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

              <Box mb="md">
                <HintedLabel
                  htmlFor={dictionaryId}
                  labelStyle={INPUT_STYLES.label}
                  hint="Pick the smallest dictionary that supplies the markers your board needs — fewer patterns means more distance between them, so fewer false detections. N×N is the marker's bit grid: 4×4 reads from farther away or at lower resolution, 7×7 is more robust but needs more pixels."
                >
                  Dictionary
                </HintedLabel>
                <Select
                  id={dictionaryId}
                  value={board.dictionary}
                  onChange={(v) => v && patch({ dictionary: v })}
                  data={dictionaries}
                  allowDeselect={false}
                  comboboxProps={{ withinPortal: true }}
                  styles={INPUT_STYLES}
                />
              </Box>

              {board.board_type === 'charuco' ? (
                <>
                  <Group grow mb="md">
                    <NumberInput
                      label="Columns"
                      value={board.columns}
                      onChange={(v) => patch({ columns: Number(v) || 0 })}
                      min={2}
                      max={30}
                      styles={INPUT_STYLES}
                    />
                    <NumberInput
                      label="Rows"
                      value={board.rows}
                      onChange={(v) => patch({ rows: Number(v) || 0 })}
                      min={2}
                      max={30}
                      styles={INPUT_STYLES}
                    />
                  </Group>

                  <Group grow mb="md" align="flex-start">
                    {/* Absent while defining the INTRINSIC board: the intrinsic
                        solve is scale-free, so asking for a measurement there
                        only suggested it mattered to that calibration. */}
                    {active === 'extrinsic' && (
                      <NumberInput
                        label="Square size (mm)"
                        withAsterisk
                        error={measurementError}
                        placeholder="measure the print"
                        value={measurement}
                        onChange={onMeasurementChange}
                        min={1}
                        decimalScale={2}
                        step={0.5}
                        styles={INPUT_STYLES}
                      />
                    )}
                    <Box>
                      <HintedLabel
                        htmlFor={markerRatioId}
                        labelStyle={INPUT_STYLES.label}
                        hint="The ArUco marker inside each white cell, as a fraction of the square. 0.6–0.75 is the usual range (default 0.75) — it changes the printed board, not the calibration."
                      >
                        Marker ratio
                      </HintedLabel>
                      <NumberInput
                        id={markerRatioId}
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
                </>
              ) : (
                <Group grow mb="md" align="flex-start">
                  <NumberInput
                    label="Marker ID"
                    value={board.marker_id}
                    onChange={(v) => patch({ marker_id: Number(v) || 0 })}
                    min={0}
                    max={dictionaryCapacity(board.dictionary) - 1}
                    styles={INPUT_STYLES}
                  />
                  <NumberInput
                    label="Marker size (mm)"
                    // A single ArUco target is extrinsic-only, so its measurement
                    // always carries the scale — no conditional here.
                    withAsterisk
                    error={measurementError}
                    placeholder="measure the print"
                    value={measurement}
                    onChange={onMeasurementChange}
                    min={1}
                    decimalScale={2}
                    step={0.5}
                    styles={INPUT_STYLES}
                  />
                </Group>
              )}

              <Switch
                checked={board.inverted}
                onChange={(e) => patch({ inverted: e.currentTarget.checked })}
                label="Inverted (ink saving)"
              />
            </Paper>
          )}

          {/* Always present — the extrinsic choice (a board, or inherit) must be
              confirmed to complete Target Config, so it can't be skipped. */}
          <StickyActionBar>
            {/* Blocked client-side rather than letting the backend's gt=0
                rejection come back as a raw 422. */}
            <Button
              fullWidth
              mt="lg"
              onClick={save}
              loading={saving}
              disabled={missingMeasurement}
            >
              Save {active} board
            </Button>
          </StickyActionBar>
        </Box>
      </Box>
    </Box>
  );
}
