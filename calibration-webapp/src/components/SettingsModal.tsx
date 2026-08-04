import {
  ActionIcon,
  Alert,
  Box,
  Button,
  Group,
  Modal,
  NumberInput,
  Popover,
  Switch,
  Text,
} from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';
import { useEffect, useState, type ReactNode } from 'react';

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

// An info affordance next to a field label: tapping the icon opens a Popover with the
// help text, replacing the always-on `description` line. Popover, not Tooltip — it opens
// on tap and dismisses on outside-tap, so it works on the touch devices the app targets
// (a hover Tooltip is dead weight on tablet/phone). The icon lives OUTSIDE the field's
// <label> on purpose: nested inside, a tap would toggle the Switch / pop the numeric
// keyboard on a NumberInput via the label's implicit control activation.
function InfoPopover({ children }: { children: ReactNode }) {
  return (
    <Popover
      width={260}
      position="top-end"
      shadow="md"
      // Explicit opaque surface: the theme's default dropdown background is
      // see-through here, so the help text bled into the modal content behind it.
      // A raised surface (rc-input) also reads as "floating above" the modal.
      styles={{ dropdown: { background: 'var(--rc-input)', border: '1px solid var(--rc-border)' } }}
    >
      <Popover.Target>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          radius="xl"
          aria-label="More information"
        >
          <IconInfoCircle size={16} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown>
        <Text fz="0.72rem" c="dark.1" style={{ lineHeight: 1.5 }}>
          {children}
        </Text>
      </Popover.Dropdown>
    </Popover>
  );
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
        <InfoPopover>{help}</InfoPopover>
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
        <InfoPopover>
          Full-fidelity preview. Turn it off to publish a lower frame rate and spare CPU —
          recording and calibration are never affected.
        </InfoPopover>
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
