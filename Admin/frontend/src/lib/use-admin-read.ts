import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';
import { useAuthGeneration } from './use-auth-generation';

type ReadResult<T> = { data?: T; error?: { message: string } };

/** Poll one bounded read at a time; only its mounted session may publish it. */
export function useAdminRead<T>(load: () => Promise<ReadResult<T>>, intervalMs: number, validate?: (value: T) => boolean) {
    const generation = useAuthGeneration();
    const mounted = useRef(false);
    const sequence = useRef(0);
    const pending = useRef(false);
    const [state, setState] = useState<{ owner: number; data: T | null; error: string | null; loading: boolean }>({
        owner: generation, data: null, error: null, loading: true,
    });
    const refresh = useCallback(async () => {
        if (!mounted.current || generation !== api.getAuthGeneration() || pending.current) return false;
        pending.current = true;
        const request = ++sequence.current;
        const owns = () => mounted.current && request === sequence.current && generation === api.getAuthGeneration();
        try {
            const result = await load();
            if (!owns()) return false;
            if (result.data !== undefined && !result.error && (!validate || validate(result.data))) {
                setState({ owner: generation, data: result.data, error: null, loading: false });
                return true;
            } else {
                setState((before) => ({ owner: generation, data: before.owner === generation ? before.data : null,
                    error: result.error?.message ?? 'The server returned an incomplete or unexpected response.', loading: false }));
            }
        } catch (error) {
            if (owns()) setState((before) => ({ owner: generation, data: before.owner === generation ? before.data : null,
                error: error instanceof Error ? error.message : 'This information is unavailable.', loading: false }));
        } finally { if (owns()) pending.current = false; }
        return false;
    }, [generation, load, validate]);
    useEffect(() => {
        mounted.current = true;
        pending.current = false;
        void refresh();
        const timer = setInterval(() => void refresh(), intervalMs);
        return () => { mounted.current = false; pending.current = false; sequence.current += 1; clearInterval(timer); };
    }, [refresh, intervalMs]);
    return { ...(state.owner === generation ? state : { data: null, error: null, loading: true }), refresh };
}
