"use client";

import { useMemo } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { CampaignPerformanceTable } from "@/components/campaigns/campaign-performance-table";
import { EventStream } from "@/components/campaigns/event-stream";
import { AlertTimeline } from "@/components/campaigns/alert-timeline";
import { CommandBar } from "@/components/campaigns/command-bar";
import { queryKeys, useCampaigns } from "@/lib/api-hooks";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { notificationsStore } from "@/lib/notifications";

export default function CampaignsPage() {
    const qc = useQueryClient();
    const campaignsQuery = useCampaigns();
    const campaigns = useMemo(() => campaignsQuery.data ?? [], [campaignsQuery.data]);
    const loading = campaignsQuery.isLoading;
    const error = campaignsQuery.isError ? (campaignsQuery.error instanceof Error ? campaignsQuery.error.message : "Failed to load campaigns") : "";

    const pause = useMutation({
        mutationFn: (id: string) => dashboardApi.pauseCampaign(id),
        onSuccess: (_res, id) => {
            qc.setQueryData<Campaign[]>(queryKeys.campaigns(), (prev) => (prev ?? []).map((c) => (c.id === id ? { ...c, status: "paused" } : c)));
            notificationsStore.create({ type: "success", title: "Campaign paused", message: "Campaign paused successfully." });
        },
    });

    const resume = useMutation({
        mutationFn: (id: string) => dashboardApi.startCampaign(id),
        onSuccess: (_res, id) => {
            qc.setQueryData<Campaign[]>(queryKeys.campaigns(), (prev) =>
                (prev ?? []).map((c) => (c.id === id ? { ...c, status: "running", started_at: c.started_at || new Date().toISOString() } : c))
            );
            notificationsStore.create({ type: "success", title: "Campaign resumed", message: "Campaign started successfully." });
        },
        onError: (err) => {
            // The out-of-minutes 402 ships a structured detail the http
            // client exposes as `.details`; fall back to the plain message.
            const detail = (err as { details?: { message?: string } })?.details;
            const message =
                (detail && typeof detail.message === "string" && detail.message) ||
                (err instanceof Error ? err.message : "Could not start the campaign.");
            notificationsStore.create({ type: "error", title: "Can't start campaign", message });
        },
    });

    const removeCampaign = useMutation({
        // Real delete (soft-delete on the backend). Previously this called
        // stopCampaign and only filtered the row from the local cache, so the
        // campaign reappeared on refresh — the "delete doesn't work" bug.
        mutationFn: (id: string) => dashboardApi.deleteCampaign(id),
        onSuccess: (_res, id) => {
            qc.setQueryData<Campaign[]>(queryKeys.campaigns(), (prev) => (prev ?? []).filter((c) => c.id !== id));
            notificationsStore.create({ type: "success", title: "Campaign deleted", message: "Campaign removed." });
        },
        onError: (err) => {
            // Refetch so the row that failed to delete stays visible/accurate.
            void qc.invalidateQueries({ queryKey: queryKeys.campaigns() });
            const message = err instanceof Error ? err.message : "Could not delete the campaign.";
            notificationsStore.create({ type: "error", title: "Can't delete campaign", message });
        },
    });

    async function handlePause(id: string) {
        await pause.mutateAsync(id);
    }

    async function handleResume(id: string) {
        // onError already shows the toast (e.g. out-of-minutes 402); swallow
        // the rejection so it doesn't bubble as an unhandled promise error.
        try {
            await resume.mutateAsync(id);
        } catch {
            /* handled in mutation onError */
        }
    }

    async function handleDelete(id: string) {
        await removeCampaign.mutateAsync(id);
    }

    async function handleDuplicate(id: string) {
        const src = campaigns.find((c) => c.id === id);
        if (!src) return;
        const now = Date.now();
        const copy: Campaign = {
            ...src,
            id: `camp-copy-${now}`,
            name: `${src.name} - Copy`,
            status: "draft",
            calls_completed: 0,
            calls_failed: 0,
            created_at: new Date().toISOString(),
            started_at: undefined,
            completed_at: undefined,
        };
        qc.setQueryData<Campaign[]>(queryKeys.campaigns(), (prev) => [copy, ...(prev ?? [])]);
    }

    async function handleUpdate(next: Campaign) {
        qc.setQueryData<Campaign[]>(queryKeys.campaigns(), (prev) => (prev ?? []).map((c) => (c.id === next.id ? next : c)));
    }

    return (
        <DashboardLayout title="Campaign Performance" description="Sorting, filtering, bulk actions, and live ops signals">
            <div className="space-y-6">
                <CommandBar campaigns={campaigns} onPause={handlePause} onResume={handleResume} />
                <CampaignPerformanceTable
                    campaigns={campaigns}
                    loading={loading}
                    error={error}
                    onPause={handlePause}
                    onResume={handleResume}
                    onDelete={handleDelete}
                    onDuplicate={handleDuplicate}
                    onUpdate={handleUpdate}
                />
                <EventStream campaigns={campaigns} />
                <AlertTimeline campaigns={campaigns} />
            </div>
        </DashboardLayout>
    );
}
