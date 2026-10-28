import { useCallback, useLayoutEffect, useRef } from 'react';
import { api } from './api';

/** Capture the committed view/session before an async operation; discard work after that owner changes. */
export function useOperationOwner(generation: number, scope: string) {
    const committed = useRef<{ generation: number; scope: string } | null>(null);
    useLayoutEffect(() => {
        const owner = { generation, scope };
        committed.current = owner;
        return () => { if (committed.current === owner) committed.current = null; };
    }, [generation, scope]);
    return useCallback(() => {
        const owner = committed.current;
        return () => owner !== null && owner === committed.current && owner.generation === generation
            && owner.scope === scope && generation === api.getAuthGeneration();
    }, [generation, scope]);
}
