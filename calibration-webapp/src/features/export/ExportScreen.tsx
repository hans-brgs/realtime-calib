import {
  Badge,
  Box,
  Button,
  Center,
  Checkbox,
  Group,
  SegmentedControl,
  Select,
  Stack,
  Text,
} from '@mantine/core';
import { IconDownload, IconFileExport, IconLock } from '@tabler/icons-react';
import { useState } from 'react';

import { useAppDispatch, useAppSelector } from '@/app/hooks';
import { ScreenHeader } from '@/components/ScreenHeader';
import {
  CONVENTIONS,
  conventionByValue,
  conventionSelected,
  selectConvention,
} from '@/features/review3d/conventions';
import { selectSession } from '@/features/session/sessionSlice';
import {
  type ExportedFile,
  exportArchiveUrl,
  exportCalibration,
} from '@/transport/httpClient';

// Export screen (spec calibration-export, merged model): ONE Artifacts card —
// "you export what you see". The convention dropdown (shared state with the 3D
// review) and the units selector live at the top; the variant matching the
// displayed convention is checked + LOCKED (always exported, badge "3D view"),
// the canonical Caliscope TOML is always included, other artifacts are additive.
const VARIANTS = CONVENTIONS.filter((c) => c.exportFormat !== null);

export function ExportScreen() {
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  const conventionId = useAppSelector(selectConvention);
  const convention = conventionByValue(conventionId);
  const cameras = session?.cameras ?? [];
  const ready = cameras.length > 0 && cameras.every((c) => c.rotation != null);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [units, setUnits] = useState<'mm' | 'm'>('mm');
  const [files, setFiles] = useState<ExportedFile[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // "Export what you see": the variant of the displayed convention is not a
  // preference to track — it is force-included at export time (locked checkbox).
  const lockedFormat = convention.exportFormat;

  const toggle = (format: string, checked: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(format);
      } else {
        next.delete(format);
      }
      return next;
    });
  };

  const runExport = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const formats = new Set(selected);
      if (lockedFormat) {
        formats.add(lockedFormat);
      }
      const response = await exportCalibration([...formats], units);
      setFiles(response.files);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'export failed');
    } finally {
      setBusy(false);
    }
  };

  if (!ready) {
    return (
      <Box p={{ base: 'md', sm: 'xl' }} h="100%">
        <ScreenHeader
          title="Export"
          subtitle="Caliscope-compatible camera_array.toml + per-platform integration files."
        />
        <Center h="60%">
          <Stack align="center" gap={8}>
            <IconLock size={28} color="var(--mantine-color-dark-3)" />
            <Text c="dark.3" fz="0.84rem">
              Complete the extrinsic calibration first — the export needs every
              camera's pose.
            </Text>
          </Stack>
        </Center>
      </Box>
    );
  }

  return (
    <Box p={{ base: 'md', sm: 'xl' }} h="100%" style={{ overflowY: 'auto' }}>
      <ScreenHeader
        title="Export"
        subtitle="Caliscope-compatible camera_array.toml + per-platform integration files."
      />
      <Box maw={560}>
        <Box
          p="md"
          mb="md"
          style={{
            border: '1px solid var(--rc-border)',
            borderRadius: 'var(--mantine-radius-lg)',
            background: 'var(--rc-panel)',
          }}
        >
          <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="sm" style={{ letterSpacing: '0.07em' }}>
            Artifacts — you export what you see
          </Text>
          <Group gap="sm" align="flex-end" wrap="wrap">
            <Select
              flex={1}
              miw={260}
              label="Displayed convention (3D review)"
              value={conventionId}
              onChange={(value) => value && dispatch(conventionSelected(value))}
              data={CONVENTIONS.map(({ value, detail }) => ({ value, label: detail }))}
              comboboxProps={{ withinPortal: true }}
              styles={{ label: { fontSize: '0.66rem', color: 'var(--mantine-color-dark-3)' } }}
            />
            <SegmentedControl
              size="xs"
              value={units}
              onChange={(value) => setUnits(value as 'mm' | 'm')}
              data={[
                { label: 'mm', value: 'mm' },
                { label: 'm', value: 'm' },
              ]}
            />
          </Group>
          <Text fz="0.66rem" c="dark.3" mt={6} mb="sm">
            The variant of the displayed convention is always exported. Units apply
            to the platform JSONs — the canonical camera_array.toml stays OpenCV, mm.
          </Text>
          <Stack gap="sm">
            <Checkbox
              checked
              disabled
              label={
                <Group gap={8} wrap="nowrap">
                  <span>camera_array.toml — Caliscope · canonical OpenCV</span>
                  {lockedFormat === null && (
                    <Badge size="xs" variant="light" color="violet" style={{ flex: 'none' }}>
                      3D view
                    </Badge>
                  )}
                </Group>
              }
            />
            <Checkbox
              checked={selected.has('aniposelib')}
              onChange={(e) => toggle('aniposelib', e.currentTarget.checked)}
              label="camera_array_aniposelib.toml — aniposelib · canonical OpenCV"
            />
            {VARIANTS.map((variant) => {
              const format = variant.exportFormat ?? '';
              const locked = format === lockedFormat;
              return (
                <Checkbox
                  key={variant.value}
                  checked={locked || selected.has(format)}
                  disabled={locked}
                  onChange={(e) => toggle(format, e.currentTarget.checked)}
                  label={
                    <Group gap={8} wrap="nowrap">
                      <span>{`camera_array_${format}.json — ${variant.detail}`}</span>
                      {locked && (
                        <Badge size="xs" variant="light" color="violet" style={{ flex: 'none' }}>
                          3D view
                        </Badge>
                      )}
                    </Group>
                  }
                />
              );
            })}
          </Stack>
          <Text fz="0.66rem" c="dark.3" mt="sm" className="rc-tnum">
            Destination: {session?.session_dir ?? 'sessions/…'}/export/
          </Text>
          <Button
            mt="sm"
            fullWidth
            loading={busy}
            leftSection={<IconFileExport size={16} />}
            onClick={() => void runExport()}
          >
            Export
          </Button>
          {message && (
            <Text fz="0.72rem" c="var(--rc-error)" mt="sm">
              {message}
            </Text>
          )}
        </Box>

        {files && (
          <Box
            p="md"
            style={{
              border: '1px solid var(--rc-border)',
              borderRadius: 'var(--mantine-radius-lg)',
              background: 'var(--rc-panel)',
            }}
          >
            <Text fz="0.66rem" fw={600} c="dark.3" tt="uppercase" mb="sm" style={{ letterSpacing: '0.07em' }}>
              Exported files
            </Text>
            <Stack gap={6}>
              {files.map((file) => (
                <Group key={file.name} justify="space-between" wrap="nowrap">
                  <Text fz="0.78rem" className="rc-tnum" style={{ wordBreak: 'break-all' }}>
                    {file.name}
                  </Text>
                  <Badge size="sm" variant="light" color="gray" style={{ flex: 'none' }}>
                    {file.convention}
                  </Badge>
                </Group>
              ))}
            </Stack>
            <Button
              mt="md"
              fullWidth
              variant="light"
              component="a"
              href={exportArchiveUrl()}
              leftSection={<IconDownload size={16} />}
            >
              Download all (zip)
            </Button>
          </Box>
        )}
      </Box>
    </Box>
  );
}
