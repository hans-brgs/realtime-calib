import { createContext, useContext } from 'react';

import { useCompactLayout } from '@/components/layout/useCompactLayout';

// Fold state of the wizard's right-hand panel (see SidePanel.tsx).
export interface SidePanelState {
  collapsed: boolean;
  toggle: () => void;
}

export const SidePanelContext = createContext<SidePanelState>({
  collapsed: false,
  toggle: () => {},
});

// Whether the panel is folded on THIS layout: always open in `compact`, where the
// panel is stacked under the view and there is no width to give back (ADR-0041).
export function useSidePanel(): SidePanelState {
  const state = useContext(SidePanelContext);
  const compact = useCompactLayout();
  return compact ? { ...state, collapsed: false } : state;
}
