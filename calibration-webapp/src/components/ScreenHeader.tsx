import { Box, Group, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

interface ScreenHeaderProps {
  title: string;
  subtitle?: ReactNode;
  right?: ReactNode;
}

// Per-screen header: Sora screen-title (h2 / 21px) + muted subtitle, with optional
// right-aligned actions. Shared across every wizard screen.
export function ScreenHeader({ title, subtitle, right }: ScreenHeaderProps) {
  return (
    <Group justify="space-between" align="flex-start" wrap="wrap" gap="md" mb="lg">
      <Box>
        <Title order={2}>{title}</Title>
        {subtitle ? (
          <Text c="dark.2" fz="0.84rem" mt={6} maw={640}>
            {subtitle}
          </Text>
        ) : null}
      </Box>
      {right}
    </Group>
  );
}
