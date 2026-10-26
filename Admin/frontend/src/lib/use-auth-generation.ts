import { useSyncExternalStore } from 'react';
import { api } from './api';

const subscribe = (listener: () => void) => api.onAuthGenerationChanged(listener);
const snapshot = () => api.getAuthGeneration();

/** Retire session-owned views even when their parent has not rendered yet. */
export function useAuthGeneration(): number {
    return useSyncExternalStore(subscribe, snapshot, snapshot);
}
