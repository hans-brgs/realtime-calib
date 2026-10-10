import { Badge, Group, Paper, Stack, Text } from '@mantine/core';
import { useEffect, useState } from 'react';

import { InfoPopover } from '@/components/InfoPopover';
import {
  CHECK_LABELS,
  SECTION_LABEL,
  STATUS_COLORS,
  formatCheckValue,
} from '@/features/export/exportChecks';
import { type ExportCheck, fetchExportChecks } from '@/transport/httpClient';

// Read-only pre-export checks (ADR-0057). Each one says what it proves in its
// detail, behind a tap-to-open popover; "internal" ones are computed from the solve
// itself and cannot see a bias the solve shares, which is why none blocks the download.
export function ExportChecks({ revision }: { revision: string }) {
  const [checks, setChecks] = useState<ExportCheck[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetchExportChecks()
      .then((list) => {
        if (!alive) return;
        setChecks(list);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (!alive) return;
        setChecks(null);
        setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      alive = false;
    };
  }, [revision]);

  return (
    <Paper p="md" radius="lg" withBorder>
      <Text {...SECTION_LABEL} mb={8}>
        Checks before export
      </Text>
      {checks == null ? (
        <Text fz="0.74rem" c="dark.3">
          {error ? `Checks unavailable: ${error}` : 'Checks unavailable.'}
        </Text>
      ) : (
        <Stack gap={6}>
          {checks.map((check) => {
            const label = CHECK_LABELS[check.id] ?? check.id;
            return (
              <Group key={check.id} justify="space-between" wrap="nowrap" gap="xs">
                <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
                  <Badge size="xs" variant="light" color={STATUS_COLORS[check.status]}>
                    {check.status}
                  </Badge>
                  <Text fz="0.76rem" truncate>
                    {label}
                  </Text>
                  <InfoPopover label={`About ${label}`} width={280}>
                    {check.detail}
                  </InfoPopover>
                </Group>
                <Text fz="0.72rem" c="dark.2" className="rc-tnum" style={{ flex: 'none' }}>
                  {formatCheckValue(check)}
                </Text>
              </Group>
            );
          })}
          <Text fz="0.64rem" c="dark.3" mt={4}>
            Computed from the solve itself: they spot an inconsistency, they do not measure the
            scale — a tape-measured distance does.
          </Text>
        </Stack>
      )}
    </Paper>
  );
}
