import {
  Badge,
  Box,
  Button,
  Center,
  Checkbox,
  Group,
  Select,
  Stack,
  Text,
} from '@mantine/core';
import { IconDownload, IconFileExport, IconLock } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

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

// Export screen (spec calibration-export). Anti-confusion guards: the convention is
// re-displayed EXPLICITLY (up + handedness + platforms, shared state with the 3D
// review) and the artifacts are MULTI-selectable — the canonical Caliscope TOML is
// always included (locked), platform variants are additive integration files.
const VARIANTS = CONVENTIONS.filter((c) => c.exportFormat !== null);

export function ExportScreen() {
  const dispatch = useAppDispatch();
  const session = useAppSelector(selectSession);
  const conventionId = useAppSelector(selectConvention);
  const convention = conventionByValue(conventionId);
  const cameras = session?.cameras ?? [];
  const ready = cameras.length > 0 && cameras.every((c) => c.rotation != null);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<ExportedFile[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // The review's convention preselects its platform variant (added, never removes
  // the operator's other choices).
  useEffect(() => {
    if (convention.exportFormat) {
      const format = convention.exportFormat;
      setSelected((current) => new Set(current).add(format));
    }
  }, [convention.exportFormat]);

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
      const response = await exportCalibration([...selected]);
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
            Convention (platform variants)
          </Text>
          <Select
            value={conventionId}
            onChange={(value) => value && dispatch(conventionSelected(value))}
            data={CONVENTIONS.map(({ value, detail }) => ({ value, label: detail }))}
            comboboxProps={{ withinPortal: true }}
          />
          <Text fz="0.66rem" c="dark.3" mt={6}>
            Shared with the 3D review's display selector. The canonical
            camera_array.toml is NOT affected — it always stays OpenCV.
          </Text>
        </Box>

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
            Artifacts
          </Text>
          <Stack gap="sm">
            <Checkbox
              checked
              disabled
              label="camera_array.toml — Caliscope · canonical OpenCV (always included)"
            />
            <Checkbox
              checked={selected.has('aniposelib')}
              onChange={(e) => toggle('aniposelib', e.currentTarget.checked)}
              label="camera_array_aniposelib.toml — aniposelib · canonical OpenCV"
            />
            {VARIANTS.map((variant) => (
              <Checkbox
                key={variant.value}
                checked={selected.has(variant.exportFormat ?? '')}
                onChange={(e) => toggle(variant.exportFormat ?? '', e.currentTarget.checked)}
                label={`camera_array_${variant.exportFormat}.json — ${variant.detail}`}
              />
            ))}
          </Stack>
          <Button
            mt="md"
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
