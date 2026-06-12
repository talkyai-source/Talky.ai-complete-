/**
 * Canonical React Query layer for AI Options.
 *
 * Replaces the per-page `useEffect` + `useState` fetch-on-mount pattern with
 * cached, deduped, stale-while-revalidate queries. The cache is global (the
 * QueryClient lives at the app root), so navigating away and back is instant,
 * and `prefetchAiOptions` lets us warm it right after login.
 *
 * staleTime is tiered by how volatile the data is:
 *   - providers / voices  → catalogs, rarely change → 5 min
 *   - config              → tenant setting, editable → 30 s
 */
import {
    useQuery,
    type QueryClient,
} from "@tanstack/react-query";

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
    providers: () => [...aiOptionsKeys.all, "providers"] as const,
    voices: () => [...aiOptionsKeys.all, "voices"] as const,
    config: () => [...aiOptionsKeys.all, "config"] as const,
};

export type VoicesResult = { voices: VoiceInfo[]; elevenlabs_error?: string };

export function useProvidersQuery() {
    return useQuery<ProviderListResponse>({
        queryKey: aiOptionsKeys.providers(),
        queryFn: () => aiOptionsApi.getProviders(),
        staleTime: CATALOG_STALE,
    });
}

export function useVoicesQuery() {
    return useQuery<VoicesResult>({
        queryKey: aiOptionsKeys.voices(),
        queryFn: () => aiOptionsApi.getVoices(),
        staleTime: CATALOG_STALE,
    });
}

export function useConfigQuery() {
    return useQuery<AIProviderConfig>({
        queryKey: aiOptionsKeys.config(),
        queryFn: () => aiOptionsApi.getConfig(),
        staleTime: CONFIG_STALE,
    });
}

/** Warm the AI Options cache (used by the post-login prefetch). Best-effort —
 *  a slow/failing endpoint never blocks the others or throws. */
export async function prefetchAiOptions(qc: QueryClient): Promise<void> {
    await Promise.allSettled([
        qc.prefetchQuery({ queryKey: aiOptionsKeys.providers(), queryFn: () => aiOptionsApi.getProviders(), staleTime: CATALOG_STALE }),
        qc.prefetchQuery({ queryKey: aiOptionsKeys.voices(), queryFn: () => aiOptionsApi.getVoices(), staleTime: CATALOG_STALE }),
        qc.prefetchQuery({ queryKey: aiOptionsKeys.config(), queryFn: () => aiOptionsApi.getConfig(), staleTime: CONFIG_STALE }),
    ]);
}
