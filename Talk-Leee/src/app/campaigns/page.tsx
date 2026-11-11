"use client";

import { useMemo, useRef, useState } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { dashboardApi, type Campaign } from "@/lib/dashboard-api";
import { CampaignStartDialog, useCampaignStartDialog } from "@/components/campaigns/campaign-start-dialog";
import { CampaignControlNoticeBox } from "@/components/campaigns/campaign-control-notice";
import {
    canStartCampaign,
    runCampaignPause,
    runCampaignPauses,
    runCampaignStop,
    startOutcomeNotice,
    type CampaignControlNotice,
} from "@/lib/campaign-control";
import { CampaignPerformanceTable } from "@/components/campaigns/campaign-performance-table";
import { EventStream } from "@/components/campaigns/event-stream";
import { AlertTimeline } from "@/components/campaigns/alert-timeline";
import { CommandBar } from "@/components/campaigns/command-bar";
import { queryKeys, useOutboundCampaigns } from "@/lib/api-hooks";
import { useQueryClient } from "@tanstack/react-query";
import { useNotificationMutation } from "@/lib/notification-mutations";
import { useNotificationsActions } from "@/lib/notifications-client";
import { isApiClientError } from "@/lib/http-client";
import { campaignActions, useKnownPermissions } from "@/lib/permissions";

export default function CampaignsPage() {
    const { create: createNotification } = useNotificationsActions();
    const qc = useQueryClient();
    const campaignsQuery = useOutboundCampaigns();
    const campaigns = useMemo(() => campaignsQuery.data ?? [], [campaignsQuery.data]);
    const loading = campaignsQuery.isLoading;
    const error = campaignsQuery.isError ? "Could not load campaigns." : "";
    // QA F1-21: offer only the campaign actions this role may take.
    const allowed = campaignActions(useKnownPermissions());

    // Result of the last Start / Resume, Pause or Stop. `campaignId` = it was
    // asked from that campaign's details modal and is shown there; null = it
    // covers the list (Resume Selected, Pause Selected, the command bar).
    const [notice, setNotice] = useState<{ campaignId: string | null; notice: CampaignControlNotice } | null>(null);
    // Which details modal (if any) the open Start dialog was asked from.
    const startScope = useRef<string | null>(null);
    // 3.4 E4: the Export window, opened from the table's buttons or the command bar.
    const [exportOpen, setExportOpen] = useState(false);

    // Read the list again so every row shows the backend's own status.
    const refresh = () => qc.invalidateQueries({ queryKey: queryKeys.campaigns() });

    // The ONE Start / Resume confirmation, shared with the campaign detail
    // page. Every resume on this page (details modal, Resume Selected, command
    // bar) opens it; nothing here starts a campaign on its own.
    const startDialog = useCampaignStartDialog({
        onFinished: async (outcome) => {
            const result = startOutcomeNotice(outcome);
            setNotice(result ? { campaignId: startScope.current, notice: result } : null);
            await refresh();
        },
    });

    const removeCampaign = useNotificationMutation({
        // Real delete (soft-delete on the backend). Previously this called
        // stopCampaign and only filtered the row from the local cache, so the
        // campaign reappeared on refresh — the "delete doesn't work" bug.
        mutationFn: (id: string) => dashboardApi.deleteCampaign(id),
        onSuccess: (_res, id) => {
            qc.setQueryData<Campaign[]>(queryKeys.campaigns(), (prev) => (prev ?? []).filter((c) => c.id !== id));
            createNotification({ type: "success", title: "Campaign deleted", message: "Campaign removed." });
        },
        onError: (err) => {
            // Refetch so the row that failed to delete stays visible/accurate.
            void qc.invalidateQueries({ queryKey: queryKeys.campaigns() });
            const message = err instanceof Error ? err.message : "Could not delete the campaign.";
            createNotification({ type: "error", title: "Can't delete campaign", message });
        },
    });

    async function handlePause(id: string) {
        setNotice(null);
        const result = await runCampaignPause(id, refresh);
        if (result) setNotice({ campaignId: id, notice: result });
        if (result?.kind === "error") void refresh();
    }

    async function handleStop(id: string) {
        setNotice(null);
        const result = await runCampaignStop(id, refresh);
        if (result) setNotice({ campaignId: id, notice: result });
        if (result?.kind === "error") void refresh();
    }

    async function handlePauseMany(targets: Campaign[]) {
        setNotice(null);
        const result = await runCampaignPauses(targets, refresh);
        if (result) setNotice({ campaignId: null, notice: result });
    }

    function handleResume(ids: string[], detailsId?: string) {
        setNotice(null);
        startScope.current = detailsId ?? null;
        startDialog.open(
            // A set (Resume Selected, command bar) goes in whole: the dialog
            // lists by name every campaign it will not start.
            campaigns.filter((c) => ids.includes(c.id) && (detailsId === undefined || canStartCampaign(c.status))),
            { listSkipped: detailsId === undefined },
        );
    }

    // A closed details modal takes its message with it; a start still running
    // from it reports to the list instead.
    function handleNoticeDismiss() {
        startScope.current = null;
        setNotice((current) => (current?.campaignId ? null : current));
    }

    async function handleDelete(id: string) {
        await removeCampaign.mutateAsync(id);
    }

    // The copy itself is made by the shared duplicateCampaign (a real
    // POST /campaigns, see lib/campaign-duplicate); the table calls this once
    // it exists. The row is never written into the cache here: the list is
    // read again so the copy shows the backend's own values.
    async function handleDuplicate() {
        await qc.invalidateQueries({ queryKey: queryKeys.campaigns() });
    }

    return (
        <DashboardLayout title="Campaign Performance" description="Sorting, filtering, bulk actions, and live ops signals">
            <div className="space-y-6">
                <CommandBar campaigns={campaigns} onPause={handlePauseMany} onResume={handleResume} onExport={() => setExportOpen(true)} allowed={allowed} />
                {notice && notice.campaignId === null ? <CampaignControlNoticeBox notice={notice.notice} testId="campaign-list-notice" /> : null}
                <CampaignPerformanceTable
                    campaigns={campaigns}
                    loading={loading}
                    error={error}
                    errorRequestId={isApiClientError(campaignsQuery.error) ? campaignsQuery.error.requestId : undefined}
                    onRetry={() => { void campaignsQuery.refetch(); }}
                    onPause={handlePause}
                    onStop={handleStop}
                    onPauseSelected={handlePauseMany}
                    onResume={handleResume}
                    notice={notice && notice.campaignId !== null ? { campaignId: notice.campaignId, notice: notice.notice } : null}
                    onNoticeDismiss={handleNoticeDismiss}
                    onDelete={handleDelete}
                    onDuplicate={handleDuplicate}
                    allowed={allowed}
                    exportOpen={exportOpen}
                    onExportOpenChange={setExportOpen}
                />
                <EventStream campaigns={campaigns} />
                <AlertTimeline campaigns={campaigns} />
                <CampaignStartDialog dialog={startDialog} />
            </div>
        </DashboardLayout>
    );
}
