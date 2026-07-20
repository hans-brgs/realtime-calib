import { Alert, Button, Group, Modal, NumberInput, Switch, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { useAppSelector } from '@/app/hooks';
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

      <NumberInput
        label="Recording quality (JPEG)"
        description="The pixels every compute re-detects on. Higher = larger files."
        value={quality}
        onChange={(v) => setQuality(Number(v) || qualityBounds[0])}
        min={qualityBounds[0]}
        max={qualityBounds[1]}
        disabled={!loaded}
        mb="md"
      />

      <Switch
        label="Preview follows the camera fps"
        description="Full-fidelity preview. Turn off to reduce the published rate and spare CPU — recording and calibration are never affected."
        checked={followFps}
        onChange={(event) => setFollowFps(event.currentTarget.checked)}
        disabled={!loaded}
        mb="md"
      />
      {!followFps && (
        <NumberInput
          label="Preview FPS (reduced)"
          value={previewFps}
          onChange={(v) => setPreviewFps(Math.max(1, Number(v) || 1))}
          min={1}
          max={maxFps}
          disabled={!loaded}
          mb="md"
        />
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
