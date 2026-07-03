import { createSlice, type PayloadAction } from '@reduxjs/toolkit';

import type { RootState } from '@/app/store';

// 3D display/export conventions (spec calibration-export). ONE shared selection
// drives both the review scene and the export preselection — never two divergent
// pickers (anti-confusion decision). The solved data stays canonical OpenCV.
export interface ConventionMeta {
  value: string;
  label: string; // compact label for the scene selector
  detail: string; // explicit up + handedness + platforms, shown at Export
  m: number[][]; // basis change: canonical OpenCV world coords -> displayed coords
  up: [number, number, number]; // three.js camera up for this convention
  exportFormat: string | null; // backend platform-variant id (null = canonical only)
}

export const CONVENTIONS: ConventionMeta[] = [
  {
    value: 'opencv',
    label: 'OpenCV · Y↓ RH (canonical)',
    detail: 'Y-down · right-handed · OpenCV (canonical)',
    m: [
      [1, 0, 0],
      [0, 1, 0],
      [0, 0, 1],
    ],
    up: [0, -1, 0],
    exportFormat: null,
  },
  {
    value: 'yup-rh',
    label: 'Y-up RH · three.js/OpenGL',
    detail: 'Y-up · right-handed · three.js / OpenGL',
    m: [
      [1, 0, 0],
      [0, -1, 0],
      [0, 0, -1],
    ],
    up: [0, 1, 0],
    exportFormat: 'threejs',
  },
  {
    value: 'zup-rh',
    label: 'Z-up RH · Blender/ROS',
    detail: 'Z-up · right-handed · Blender / ROS',
    m: [
      [1, 0, 0],
      [0, 0, 1],
      [0, -1, 0],
    ],
    up: [0, 0, 1],
    exportFormat: 'blender',
  },
  {
    value: 'yup-lh',
    label: 'Y-up LH · Unity',
    detail: 'Y-up · left-handed · Unity',
    m: [
      [1, 0, 0],
      [0, -1, 0],
      [0, 0, 1],
    ],
    up: [0, 1, 0],
    exportFormat: 'unity',
  },
  {
    value: 'zup-lh',
    label: 'Z-up LH · Unreal',
    detail: 'Z-up · left-handed · Unreal',
    m: [
      [0, 0, 1],
      [1, 0, 0],
      [0, -1, 0],
    ],
    up: [0, 0, 1],
    exportFormat: 'unreal',
  },
];

export const conventionByValue = (value: string): ConventionMeta =>
  CONVENTIONS.find((c) => c.value === value) ?? CONVENTIONS[1];

interface ConventionState {
  value: string;
}

const initialState: ConventionState = { value: 'yup-rh' };

const conventionSlice = createSlice({
  name: 'convention',
  initialState,
  reducers: {
    conventionSelected(state, action: PayloadAction<string>) {
      state.value = action.payload;
    },
  },
});

export const { conventionSelected } = conventionSlice.actions;
export default conventionSlice.reducer;

export const selectConvention = (state: RootState): string => state.convention.value;
