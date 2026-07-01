import { IconCube, IconDownload, IconFocusCentered, IconLayoutGrid, IconTopologyStar3 } from '@tabler/icons-react';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { CameraSetupScreen } from '@/features/cameras/CameraSetupScreen';
import { DashboardScreen } from '@/features/session/DashboardScreen';
import { LoadFromFilesScreen } from '@/features/session/LoadFromFilesScreen';
import type { NavTarget } from '@/features/session/selectors';

interface ScreenRouterProps {
  view: NavTarget;
  onNavigate: (id: NavTarget) => void;
}

// Maps the active view to its screen. Dashboard and Camera Setup are live; the Load
// entry is a gated screen (Phase 3.5); the remaining stages render styled
// placeholders until their high-fidelity pass.
export function StepContent({ view, onNavigate }: ScreenRouterProps) {
  switch (view) {
    case 'session':
      return <DashboardScreen onNavigate={onNavigate} />;
    case 'load':
      return <LoadFromFilesScreen onNavigate={onNavigate} />;
    case 'cameras':
      return <CameraSetupScreen />;
    case 'boards':
      return (
        <PlaceholderScreen
          icon={IconLayoutGrid}
          title="Target Config"
          description="Define the ChArUco/ArUco board and its metric scale. High-fidelity screen coming in a later pass."
        />
      );
    case 'intrinsic':
      return (
        <PlaceholderScreen
          icon={IconFocusCentered}
          title="Intrinsics"
          description="Per-camera capture with live burn-in and coverage gauges. High-fidelity screen coming in a later pass."
        />
      );
    case 'extrinsic':
      return (
        <PlaceholderScreen
          icon={IconTopologyStar3}
          title="Extrinsics"
          description="Synchronized capture and pairwise co-visibility from the anchor. High-fidelity screen coming in a later pass."
        />
      );
    case 'review':
      return (
        <PlaceholderScreen
          icon={IconCube}
          title="Review 3D"
          description="Inspect camera frustums, set the origin, and minimize reprojection error. High-fidelity screen coming in a later pass."
        />
      );
    case 'export':
      return (
        <PlaceholderScreen
          icon={IconDownload}
          title="Export"
          description="Caliscope-compatible camera_array.toml + aniposelib. High-fidelity screen coming in a later pass."
        />
      );
    default:
      return null;
  }
}
