import { Box, Center, Text, Title } from '@mantine/core';
import type { IconProps } from '@tabler/icons-react';
import type { ComponentType } from 'react';

interface PlaceholderScreenProps {
  icon: ComponentType<IconProps>;
  title: string;
  description?: string;
}

// Styled placeholder for screens whose high-fidelity build lands in a later pass
// (Target Config, Intrinsics, Extrinsics, Review 3D, Export). Navigation, layout and
// the rail are already wired; only the screen body is pending.
export function PlaceholderScreen({ icon: Icon, title, description }: PlaceholderScreenProps) {
  return (
    <Center h="100%" p={60}>
      <Box ta="center" maw={420}>
        <Box
          mx="auto"
          style={{
            width: 54,
            height: 54,
            borderRadius: 14,
            border: '1px solid var(--mantine-color-dark-4)',
            background: '#131317',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--rc-accent)',
          }}
        >
          <Icon size={24} stroke={1.8} />
        </Box>
        <Title order={3} mt={18} mb={6}>
          {title}
        </Title>
        <Text c="dark.3" fz="0.84rem">
          {description ??
            'High-fidelity screen coming in a later pass. Navigation, layout and the collapsible rail are already wired.'}
        </Text>
      </Box>
    </Center>
  );
}
