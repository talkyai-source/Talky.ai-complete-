/**
 * Canonical React Query layer for AI Options.
 *
 * Replaces the per-page `useEffect` + `useState` fetch-on-mount pattern with
 * cached, deduped queries scoped to the verified user and tenant. The shared
 * QueryClient must never let a different account reuse a saved profile or
 * its private voice catalog.
 *
 * staleTime is tiered by how volatile the data is:
 *   - providers / voices  → catalogs, rarely change → 5 min
 *   - config              → tenant setting, editable → 30 s
 */
import {
    useQuery,
    type QueryClient,
} from "@tanstack/react-query";
import { useAuth } from "@/hooks/useAuth";

import {
    aiOptionsApi,
    type ProviderListResponse,
    type VoiceInfo,
    type AIProviderConfig,
} from "@/lib/ai-options-api";

const CATALOG_STALE = 5 * 60_000;
const CONFIG_STALE = 30_000;

export const aiOptionsKeys = {
    all: ["ai-options"] as const,
    scoped: (scope: string | null) => [...aiOptionsKeys.all, scope] as const,
    providers: (scope: string | null) => [...aiOptionsKeys.scoped(scope), "providers"] as const,
    voices: (scope: string | null) => [...aiOptionsKeys.scoped(scope), "voices"] as const,
    config: (scope: string | null) => [...aiOptionsKeys.scoped(scope), "config"] as const,
};

export type VoicesResult = { voices: VoiceInfo[]; elevenlabs_error?: string };

export function useAiOptionsScope(): string | null {
    const { user, status, loading } = useAuth();
    return !loading && status === "authenticated" && user?.id && user.tenant_id
        ? JSON.stringify([user.tenant_id, user.id]) : null;
}

async function scopedRead<T>(scope: string | null, signal: AbortSignal, read: () => Promise<T>): Promise<T> {
    if (!scope || signal.aborted) throw new DOMException("Account changed", "AbortError");
    const result = await read();
    // Even a transport that completes after cancellation cannot repopulate the
    // departing account's cache or become the new account's draft.
    if (signal.aborted) throw new DOMException("Account changed", "AbortError");
    return result;
}

const retry = (count: number, error: Error) => error.name !== "AbortError" && count < 1;

export function useProvidersQuery() {
    const scope = useAiOptionsScope();
    return useQuery<ProviderListResponse>({
        queryKey: aiOptionsKeys.providers(scope),
        queryFn: ({ signal }) => scopedRead(scope, signal, () => aiOptionsApi.getProviders()),
        enabled: Boolean(scope), retry,
        staleTime: CATALOG_STALE,
    });
}

export function useVoicesQuery() {
    const scope = useAiOptionsScope();
    return useQuery<VoicesResult>({
        queryKey: aiOptionsKeys.voices(scope),
        queryFn: ({ signal }) => scopedRead(scope, signal, () => aiOptionsApi.getVoices()),
        enabled: Boolean(scope), retry,
        staleTime: CATALOG_STALE,
    });
}

export function useConfigQuery() {
    const scope = useAiOptionsScope();
    return useQuery<AIProviderConfig>({
        queryKey: aiOptionsKeys.config(scope),
        queryFn: ({ signal }) => scopedRead(scope, signal, () => aiOptionsApi.getConfig()),
        enabled: Boolean(scope), retry,
        staleTime: CONFIG_STALE,
    });
}

/** Warm the AI Options cache (used by the post-login prefetch). Best-effort —
 *  a slow/failing endpoint never blocks the others or throws. */
export async function prefetchAiOptions(qc: QueryClient, scope: string | null, lifetime: AbortSignal): Promise<void> {
    if (!scope || lifetime.aborted) return;
    await Promise.allSettled([
        qc.prefetchQuery({ queryKey: aiOptionsKeys.providers(scope), queryFn: ({ signal }) => scopedRead(scope, AbortSignal.any([signal, lifetime]), () => aiOptionsApi.getProviders()), staleTime: CATALOG_STALE, retry }),
        qc.prefetchQuery({ queryKey: aiOptionsKeys.voices(scope), queryFn: ({ signal }) => scopedRead(scope, AbortSignal.any([signal, lifetime]), () => aiOptionsApi.getVoices()), staleTime: CATALOG_STALE, retry }),
        qc.prefetchQuery({ queryKey: aiOptionsKeys.config(scope), queryFn: ({ signal }) => scopedRead(scope, AbortSignal.any([signal, lifetime]), () => aiOptionsApi.getConfig()), staleTime: CONFIG_STALE, retry }),
    ]);
}
