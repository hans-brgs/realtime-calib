import { useDispatch, useSelector } from 'react-redux';

import type { AppDispatch, RootState } from '@/app/store';

// Typed hooks (ADR-0010): always use these, never the raw react-redux hooks.
export const useAppDispatch = useDispatch.withTypes<AppDispatch>();
export const useAppSelector = useSelector.withTypes<RootState>();
