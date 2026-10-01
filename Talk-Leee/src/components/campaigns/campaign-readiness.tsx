"use client";

import { useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface ReadinessResponse {
    campaign_id: string;
    ready: boolean;
    reason_code: string;
    reason: string | null;
    caller_id: string | null;
    trunk_id: string | null;
}

export function useCampaignReadiness(campaignIds: string[], enabled = true) {
    const ids = [...new Set(campaignIds)].filter(Boolean);
    const queries = useQueries({ queries: ids.map((id) => ({
        queryKey: ["campaign-readiness", id],
        queryFn: ({ signal }: { signal: AbortSignal }) => api.request<ReadinessResponse>({ path: `/campaigns/${encodeURIComponent(id)}/readiness`, signal }),
        enabled,
        retry: false,
        staleTime: 0,
        refetchInterval: enabled ? 10000 : false,
    })) });
    const checking = enabled && queries.some((query) => query.isPending || query.isFetching);
    const failed = queries.find((query) => query.isError);
    const blocked = queries.find((query) => query.data?.ready !== true);
    const ready = enabled && ids.length > 0 && !checking && !failed && !blocked;
    const reason = !ids.length ? "No campaigns selected." : failed ? "Calling readiness could not be checked. Try again." : checking ? "Checking calling readiness…" : blocked?.data?.reason || (!ready ? "The calling route is not ready." : null);
    return { ready, checking, reason, error: Boolean(failed), retry: () => Promise.all(queries.map((query) => query.refetch())) };
}

export function CampaignReadinessNotice({ readiness }: { readiness: ReturnType<typeof useCampaignReadiness> }) {
    if (readiness.ready) return null;
    return <div className="mt-1 max-w-sm text-xs text-muted-foreground" role="status">
        {readiness.reason}{" "}
        {!readiness.checking && <button type="button" className="font-semibold underline" onClick={() => void readiness.retry()}>Check again</button>}
    </div>;
}

/** A launch surface only: backend remains authoritative and rechecks at POST. */
export function CampaignStartControl({ campaignIds, onStart, label, menu = false }: {
    campaignIds: string[];
    onStart: () => Promise<void>;
    label: string;
    menu?: boolean;
}) {
    const readiness = useCampaignReadiness(campaignIds);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const running = useRef(false);
    async function start() {
        if (!readiness.ready || running.current) return;
        running.current = true;
        setPending(true);
        setError(null);
        try { await onStart(); }
        catch (err) { setError(err instanceof Error ? err.message : "Campaign could not be started."); }
        finally { running.current = false; setPending(false); }
    }
    return <div className={menu ? "px-3 py-2" : ""}>
        <Button type="button" role={menu ? "menuitem" : undefined} size="sm" variant={menu ? "ghost" : "outline"} onClick={() => void start()} disabled={!readiness.ready || pending}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}{label}
        </Button>
        <CampaignReadinessNotice readiness={readiness} />
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    </div>;
}
