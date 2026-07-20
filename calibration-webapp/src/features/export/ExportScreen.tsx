import { CodeHighlightTabs } from '@mantine/code-highlight';
import {
  Alert,
  Badge,
  Box,
  Button,
  Center,
  Checkbox,
  Group,
  SegmentedControl,
  Stack,
  Text,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconDownload,
  IconFileTypeXml,
  IconJson,
  IconLock,
} from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { useAppSelector } from '@/app/hooks';
import { ScreenHeader } from '@/components/ScreenHeader';
import { selectDefaults } from '@/features/session/defaultsSlice';
import { selectSession } from '@/features/session/sessionSlice';
import {
  type ExportTarget,
  type PreviewFile,
  exportArchiveUrl,
  exportCalibration,
  fetchExportTargets,
  previewExport,
  saveExportConfig,
} from '@/transport/httpClient';

// Export screen (spec calibration-export, ADR-0026): the convention is an output
// codec, not a framing choice — so the operator picks TARGETS (destination software),
// all optional. The backend owns the catalog + the dry-run preview; the config
// (units + targets) is persisted on the session and restored on reopen. Layout
// mirrors the other screens (flex column, minmax(0,1fr) + fixed panel, internal
// scroll — the page itself never scrolls): preview left, controls right.
const SECTION_LABEL = {
  fz: '0.66rem',
  fw: 600,
  c: 'dark.3',
  tt: 'uppercase',
  style: { letterSpacing: '0.07em' },
} as const;

export function ExportScreen() {
  const session = useAppSelector(selectSession);
  const defaults = useAppSelector(selectDefaults);
  const cameras = session?.cameras ?? [];
  const posed = cameras.filter((c) => c.rotation != null).length;
  const ready = cameras.length > 0 && posed === cameras.length;

  const [targets, setTargets] = useState<ExportTarget[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  // Pre-restore placeholder only — the restore effect overwrites it from the
  // session preference (itself seeded backend-side from TUNING, ADR-0036).
  const [units, setUnits] = useState<'mm' | 'm'>('m');
  const [preview, setPreview] = useState<PreviewFile[]>([]);
  const [initialized, setInitialized] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Catalog of selectable targets — single source is the backend (no conventions.ts).
  useEffect(() => {
    let alive = true;
    fetchExportTargets()
      .then((list) => alive && setTargets(list))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  // Restore the persisted export config once the session AND the served defaults
  // are available (the defaults are the fallback for sessions that never chose).
  useEffect(() => {
    if (initialized || !session || !defaults) return;
    setUnits(session.export_units ?? defaults.export_units);
    const saved = session.export_targets ?? [];
    setSelected(saved.length > 0 ? saved : ['caliscope']);
    setInitialized(true);
  }, [initialized, session, defaults]);

  // On every change: refresh the dry-run preview and persist the config.
  useEffect(() => {
    if (!ready || !initialized) return;
    if (selected.length === 0) {
      setPreview([]);
      return;
    }
    let alive = true;
    previewExport(selected, units)
      .then((files) => alive && setPreview(files))
      .catch(() => alive && setPreview([]));
    saveExportConfig(selected, units).catch(() => {});
    return () => {
      alive = false;
    };
  }, [ready, initialized, selected, units]);

  const toggle = (id: string, checked: boolean) =>
    setSelected((current) => (checked ? [...current, id] : current.filter((x) => x !== id)));

  const download = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await exportCalibration(selected, units);
      window.location.href = exportArchiveUrl();
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
          subtitle="Camera calibration files for Caliscope and 3D engines."
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

  const codeTabs = preview.map((file) => ({
    fileName: file.name,
    code: file.content,
    language: file.language,
  }));
  const destination = `${session?.session_dir ?? 'sessions/…'}/export/`;

  return (
    <Box p={{ base: 'md', sm: 'xl' }} h="100%" style={{ display: 'flex', flexDirection: 'column' }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <ScreenHeader
          title="Export"
          subtitle="Camera calibration files for Caliscope and 3D engines."
        />
        <Badge color="teal" variant="light" size="lg" mt={6} style={{ flex: 'none' }}>
          {posed} / {cameras.length} cameras posed
        </Badge>
      </Group>

      <Box
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 340px',
          gap: 22,
        }}
      >
        {/* LEFT — preview fills the height; only the code block scrolls. */}
        <Box style={{ minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <Text {...SECTION_LABEL} mb={8}>
            Preview
          </Text>
          {codeTabs.length > 0 ? (
            <Box
              style={{
                flex: 1,
                minHeight: 0,
                overflow: 'auto',
                border: '1px solid var(--rc-border)',
                borderRadius: 'var(--mantine-radius-md)',
              }}
            >
              <CodeHighlightTabs withCopyButton withLineNumbers radius={0} code={codeTabs} />
            </Box>
          ) : (
            <Center
              style={{
                flex: 1,
                border: '1px dashed var(--rc-border)',
                borderRadius: 'var(--mantine-radius-md)',
              }}
            >
              <Text c="dark.3" fz="0.8rem">
                Select at least one file to preview
              </Text>
            </Center>
          )}
        </Box>

        {/* RIGHT — controls; the panel scrolls internally, never the page. */}
        <Box
          style={{
            minHeight: 0,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 'var(--mantine-spacing-lg)',
          }}
        >
          <Box>
            <Text {...SECTION_LABEL} mb={8}>
              World units
            </Text>
            <SegmentedControl
              fullWidth
              value={units}
              onChange={(value) => setUnits(value as 'mm' | 'm')}
              data={[
                { label: 'mm', value: 'mm' },
                { label: 'm', value: 'm' },
              ]}
            />
            <Text fz="0.66rem" c="dark.2" mt={6}>
              Applies to the platform JSON files — the Caliscope TOML is always in mm.
            </Text>
          </Box>

          <Box>
            <Text {...SECTION_LABEL}>Calibration files</Text>
            <Text fz="0.68rem" c="dark.2" mt={2} mb={8}>
              One file per selected target.
            </Text>
            <Stack gap={8}>
              {targets.map((target) => {
                const checked = selected.includes(target.id);
                return (
                  <Group
                    key={target.id}
                    justify="space-between"
                    wrap="nowrap"
                    gap={8}
                    p="xs"
                    style={{
                      border: `1px solid ${checked ? 'var(--rc-accent)' : 'var(--rc-border)'}`,
                      borderRadius: 'var(--mantine-radius-md)',
                      background: checked ? 'rgba(167,139,250,0.08)' : 'var(--rc-panel)',
                    }}
                  >
                    <Checkbox
                      checked={checked}
                      onChange={(e) => toggle(target.id, e.currentTarget.checked)}
                      styles={{ labelWrapper: { minWidth: 0 } }}
                      label={
                        <Box style={{ minWidth: 0 }}>
                          <Text fz="0.8rem" className="rc-tnum" style={{ wordBreak: 'break-all' }}>
                            {target.filename}
                          </Text>
                          <Text fz="0.66rem" c="dark.2">
                            {target.label}
                          </Text>
                        </Box>
                      }
                    />
                    <Badge
                      size="sm"
                      variant="light"
                      color="gray"
                      leftSection={
                        target.kind === 'toml' ? (
                          <IconFileTypeXml size={11} />
                        ) : (
                          <IconJson size={11} />
                        )
                      }
                      style={{ flex: 'none' }}
                    >
                      {target.kind.toUpperCase()}
                    </Badge>
                  </Group>
                );
              })}
            </Stack>
          </Box>

          <Box
            p="md"
            style={{
              border: '1px solid var(--rc-border)',
              borderRadius: 'var(--mantine-radius-lg)',
              background: 'var(--rc-panel)',
            }}
          >
            <Text {...SECTION_LABEL} mb={4}>
              Destination
            </Text>
            <Text fz="0.78rem" c="dark.1" mb={8}>
              Session folder → export/
            </Text>
            <Box
              p="xs"
              style={{
                border: '1px solid var(--rc-border)',
                borderRadius: 'var(--mantine-radius-sm)',
                background: 'var(--rc-input)',
              }}
            >
              <Text fz="0.74rem" className="rc-tnum" style={{ wordBreak: 'break-all' }}>
                {destination}
              </Text>
            </Box>
            <Group justify="space-between" mt="md" mb="sm">
              <Text {...SECTION_LABEL}>Selected</Text>
              <Text fz="0.8rem" className="rc-tnum" c={selected.length ? 'dark.0' : 'dark.3'}>
                {selected.length} file(s)
              </Text>
            </Group>
            <Button
              fullWidth
              loading={busy}
              disabled={selected.length === 0}
              leftSection={<IconDownload size={16} />}
              onClick={() => void download()}
            >
              Download all (zip)
            </Button>
            {message && (
              // Inline, persistent (ADR-0036): an export refusal names what to
              // fix — it must not read as a transient blip.
              <Alert
                color="red"
                variant="light"
                icon={<IconAlertTriangle size={16} />}
                title="Export failed"
                mt="sm"
              >
                <Text fz="0.72rem">{message}</Text>
              </Alert>
            )}
          </Box>
        </Box>
      </Box>
    </Box>
  );
}
