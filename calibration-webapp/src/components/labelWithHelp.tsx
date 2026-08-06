import { Group, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import { InfoPopover } from '@/components/InfoPopover';

// Props to SPREAD onto a Mantine input to put the help affordance on its label:
//
//   <Select {...labelWithHelp('Dictionary', <>…</>)} data={…} />
//
// A props factory rather than a component, and deliberately not a hand-rolled label:
// ADR-free convention, but 11dbf00 and #34 both deleted a `FieldLabel` that replaced
// Mantine's label slot with a sibling <Text>, losing the aria wiring. This keeps the
// slot — Mantine still renders <label for> and still owns `description` /
// aria-describedby — and only enriches its CONTENT.
//
// Enriching that content has one cost, measured in Chrome's accessibility tree
// against a plain-string control: a <label> containing interactive content stops
// contributing the field's accessible name, and the input ends up with NO name at
// all. `aria-label` restores it, identical to the visible text. That repair is the
// whole reason this is one factory returning both props instead of a component the
// caller could use while forgetting the second half.
export function labelWithHelp(
  label: string,
  help: ReactNode,
  // Renders the asterisk inside the label rather than through the input's
  // `withAsterisk`, which appends it after the whole label node — i.e. after the
  // icon, detached from the words it qualifies.
  options?: { required?: boolean },
) {
  return {
    'aria-label': label,
    label: (
      // A <span> host, not Group's default <div>: a label only admits phrasing content.
      <Group
        component="span"
        display="inline-flex"
        gap={2}
        wrap="nowrap"
        style={{ verticalAlign: 'middle' }}
      >
        <span>
          {label}
          {options?.required && (
            <Text component="span" c="var(--mantine-color-error)" inherit aria-hidden>
              {' *'}
            </Text>
          )}
        </span>
        <InfoPopover label={`About ${label}`}>{help}</InfoPopover>
      </Group>
    ),
  };
}
