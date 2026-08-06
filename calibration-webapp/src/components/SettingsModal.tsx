import { Alert, Box, Button, Group, Modal, NumberInput, Switch, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useEffect, useState, type ReactNode } from 'react';

import { useAppSelector } from '@/app/hooks';
import { FieldHint } from '@/components/FieldHint';
import { selectDefaults } from '@/features/session/defaultsSlice';
import { errorMessage, fetchSettings, saveSettings } from '@/transport/httpClient';

// Rig-level operator settings (ADR-0036): hardware/CPU trade-offs, not session
// state — they persist service-side (settings.toml) across sessions. Values come
// from GET /settings, bounds from GET /defaults; Apply is a full-replace PUT.
// Changes take effect live (no capture interruption).
interface SettingsModalProps {
  opened: boolean;
  onClose: () => void;
}

// Label row + info icon above a control rendered WITHOUT its built-in label (it carries
// an aria-label instead), so an info tap never activates the control.
function SettingRow({
  label,
  help,
  children,
}: {
  label: string;
  help: ReactNode;
  children: ReactNode;
}) {
  return (
    <Box mb="md">
      <Group gap={6} wrap="nowrap" align="center" mb={6}>
        <Text fz="0.84rem" fw={500}>
          {label}
        </Text>
        <FieldHint about={label}>{help}</FieldHint>
      </Group>
      {children}
    </Box>
  );
}

export function SettingsModal({ opened, onClose }: SettingsModalProps) {
  const defaults = useAppSelector(selectDefaults);

  const [quality, setQuality] = useState(0);
  const [followFps, setFollowFps] = useState(true);
  const [previewFps, setPreviewFps] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const qualityBounds = defaults?.record_quality_bounds ?? [85, 100];
  const maxFps = defaults ? Math.max(...defaults.fps_options) : 30;

  // (Re)load the current values every time the modal opens.
  useEffect(() => {
    if (!opened) return;
    let alive = true;
    setLoaded(false);
    setError(null);
    fetchSettings()
      .then((settings) => {
        if (!alive) return;
        setQuality(settings.record_quality);
        setFollowFps(settings.preview_fps === null);
        // Seed the reduced-rate input even while "follow" is on, so toggling the
        // switch lands on a sensible served value rather than an empty field.
        setPreviewFps(
          settings.preview_fps ?? defaults?.preview_fps_options[0] ?? maxFps,
        );
        setLoaded(true);
      })
      .catch((cause) => alive && setError(errorMessage(cause, 'failed to load settings')));
    return () => {
      alive = false;
    };
  }, [opened, defaults, maxFps]);

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      await saveSettings({
        record_quality: quality,
        preview_fps: followFps ? null : previewFps,
      });
      onClose();
    } catch (cause) {
      setError(errorMessage(cause, 'failed to save settings'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Settings" centered size="sm">
      <Text fz="0.72rem" c="dark.2" mb="md">
        Rig-level preferences — persisted on the service, shared by every session.
        Changes apply live.
      </Text>

      <SettingRow
        label="Recording quality (JPEG)"
        help="The pixels every compute re-detects on. Higher quality means larger recording files."
      >
        <NumberInput
          aria-label="Recording quality (JPEG)"
          value={quality}
          onChange={(v) => setQuality(Number(v) || qualityBounds[0])}
          min={qualityBounds[0]}
          max={qualityBounds[1]}
          disabled={!loaded}
        />
      </SettingRow>

      <Group justify="space-between" wrap="nowrap" gap="sm" mb="md">
        <Switch
          label="Preview follows the camera fps"
          checked={followFps}
          onChange={(event) => setFollowFps(event.currentTarget.checked)}
          disabled={!loaded}
        />
        <FieldHint about="Preview follows the camera fps">
          Full-fidelity preview. Turn it off to publish a lower frame rate and spare CPU — recording
          and calibration are never affected.
        </FieldHint>
      </Group>
      {!followFps && (
        <SettingRow
          label="Preview FPS (reduced)"
          help="The reduced rate published for the live preview. Recording and calibration stay at the camera's full rate."
        >
          <NumberInput
            aria-label="Preview FPS (reduced)"
            value={previewFps}
            onChange={(v) => setPreviewFps(Math.max(1, Number(v) || 1))}
            min={1}
            max={maxFps}
            disabled={!loaded}
          />
        </SettingRow>
      )}

      {error && (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Cancel
        </Button>
        <Button color="violet" onClick={apply} loading={busy} disabled={!loaded}>
          Apply
        </Button>
      </Group>
    </Modal>
  );
}
