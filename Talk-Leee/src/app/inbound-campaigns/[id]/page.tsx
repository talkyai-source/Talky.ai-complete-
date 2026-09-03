"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Archive, ArrowLeft, Edit3, History, Pause, Play, RefreshCw, ShieldCheck } from "lucide-react";

import { CallIssuesPanel } from "@/components/campaigns/call-issues-panel";
import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { LiveCallsPanel } from "@/components/campaigns/live-calls-panel";
import { RejectedInboundCallsPanel } from "@/components/campaigns/rejected-inbound-calls-panel";
import { InboundErrorState, InboundLoadingState, InboundPermissionState } from "@/components/inbound/inbound-page-state";
import { InboundReadinessChecklist, InboundStatusBadge, ReadinessBadge } from "@/components/inbound/inbound-status";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useAuth } from "@/hooks/useAuth";
import { inboundErrorKind } from "@/lib/inbound-api";
import { getInboundCapabilities } from "@/lib/inbound-permissions";
import {
    useActivateInboundCampaign,
    useArchiveInboundCampaign,
    useEffectivePermissions,
    useInboundCampaign,
    useInboundReadiness,
    usePauseInboundCampaign,
} from "@/lib/queries/inbound-queries";

type LifecycleAction = "activate" | "deactivate" | "archive";

export default function InboundCampaignDetailPage() {
    const params = useParams<{ id: string }>();
    const id = typeof params.id === "string" ? params.id : "";
    const { user } = useAuth();
    const permissions = useEffectivePermissions();
    const capabilities = getInboundCapabilities(user?.role, permissions.isSuccess ? permissions.data.permissions : undefined);
    const canLoadCampaign = permissions.isSuccess && capabilities.canView;
    const campaignQuery = useInboundCampaign(id, canLoadCampaign);
    const readinessQuery = useInboundReadiness(id, canLoadCampaign);
    const activate = useActivateInboundCampaign(id);
    const pause = usePauseInboundCampaign(id);
    const archive = useArchiveInboundCampaign(id);
    const [action, setAction] = useState<LifecycleAction | null>(null);
    const campaign = campaignQuery.data;
    const serverCampaignId = campaign?.id.trim() ?? "";
    const readiness = readinessQuery.data ?? campaign?.readiness;
    const canActivate = readinessQuery.isSuccess && readinessQuery.data.ready;
    const canManageKnowledge = permissions.isSuccess && capabilities.canEdit;

    async function confirmAction() {
        if (!action || !campaign) return;
        const mutation = action === "activate" ? activate : action === "archive" ? archive : pause;
        try {
            await mutation.mutateAsync(campaign.version);
        } catch (error) {
            const kind = inboundErrorKind(error);
            if (kind === "conflict") throw new Error("This campaign changed in another session. Refresh before changing live routing.");
            if (kind === "forbidden") throw new Error("Your current permissions do not allow this lifecycle change.");
            throw error;
        }
    }

    const actionCopy = action === "activate"
        ? { title: "Activate inbound calling?", description: "This changes live public call routing.", warning: `New calls to ${campaign?.phone_number?.masked_number ?? "this number"} will route to the selected AI agent.`, label: "Activate calling", pending: "Activating…" }
        : action === "archive"
            ? { title: "Archive inbound campaign?", description: "Archived campaigns are permanently read-only.", warning: "This inactive campaign cannot be activated or edited after it is archived. Its call history remains available.", label: "Archive campaign", pending: "Archiving…" }
            : { title: "Deactivate inbound calling?", description: "New calls will stop routing to the AI agent.", warning: "Current calls are not interrupted. New calls fail closed and are not answered by the AI until this campaign is reactivated.", label: "Deactivate calling", pending: "Deactivating…" };

    return (
        <DashboardLayout title="Inbound Campaign" description="Live routing state, server readiness, and versioned configuration">
            {permissions.isLoading ? <InboundLoadingState label="Checking inbound permissions…" /> : permissions.isError ? (
                <InboundErrorState title="Permissions could not be verified" message="Campaign data and live routing controls remain unavailable until server permissions can be confirmed." onRetry={() => void permissions.refetch()} />
            ) : !capabilities.canView ? <InboundPermissionState /> : campaignQuery.isLoading ? <InboundLoadingState /> : campaignQuery.isError || !campaign ? (
                <InboundErrorState title={(campaignQuery.error as { status?: number } | undefined)?.status === 404 ? "Inbound campaign not found" : "Campaign is unavailable"} message={campaignQuery.error instanceof Error ? campaignQuery.error.message : "This campaign may have been removed or you may not have access."} onRetry={() => void campaignQuery.refetch()} />
            ) : (
                <div className="space-y-5">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <Button asChild variant="ghost" size="sm" className="self-start px-2"><Link href="/inbound-campaigns"><ArrowLeft className="h-4 w-4" aria-hidden />Back to inbound campaigns</Link></Button>
                        <div className="flex flex-wrap gap-2">
                            {serverCampaignId && capabilities.canEdit && campaign.status !== "active" && campaign.status !== "archived" ? <Button asChild variant="outline" size="sm"><Link href={`/inbound-campaigns/${encodeURIComponent(serverCampaignId)}/edit`}><Edit3 className="h-4 w-4" aria-hidden />Edit configuration</Link></Button> : null}
                            {serverCampaignId ? <Button asChild variant="outline" size="sm"><Link href={`/calls?direction=inbound&inbound_campaign_id=${encodeURIComponent(serverCampaignId)}`}><History className="h-4 w-4" aria-hidden />Call history</Link></Button> : null}
                            {capabilities.canChangeLifecycle && (campaign.status === "draft" || campaign.status === "paused") ? <Button size="sm" disabled={!canActivate} onClick={() => setAction("activate")}><Play className="h-4 w-4" aria-hidden />Activate</Button> : null}
                            {capabilities.canChangeLifecycle && campaign.status === "active" ? <Button size="sm" variant="outline" onClick={() => setAction("deactivate")}><Pause className="h-4 w-4" aria-hidden />Deactivate</Button> : null}
                            {capabilities.canChangeLifecycle && (campaign.status === "draft" || campaign.status === "paused") ? <Button size="sm" variant="destructive" onClick={() => setAction("archive")}><Archive className="h-4 w-4" aria-hidden />Archive</Button> : null}
                        </div>
                    </div>

                    <section className="content-card" aria-labelledby="inbound-overview-heading">
                        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                            <div className="min-w-0"><p className="font-mono text-sm text-muted-foreground">{campaign.phone_number?.masked_number ?? "No verified number"}</p><h2 id="inbound-overview-heading" className="mt-1 break-words text-2xl font-semibold text-foreground">{campaign.name}</h2>{campaign.purpose ? <p className="mt-1 text-sm text-muted-foreground">{campaign.purpose}</p> : null}</div>
                            <div className="flex flex-wrap gap-2"><InboundStatusBadge status={campaign.status} /><ReadinessBadge readiness={readiness ?? campaign.readiness} /></div>
                        </div>
                        <dl className="mt-6 grid gap-4 border-t border-border pt-5 sm:grid-cols-2 lg:grid-cols-4"><Info label="AI campaign" value={campaign.campaign_name || campaign.campaign_id || "Not selected"} /><Info label="SIP trunk" value={campaign.sip_trunk_name || campaign.sip_trunk_id || "Not selected"} /><Info label="Opening" value={campaign.opening_mode === "agent_first" ? "Agent first" : "Caller first"} /><Info label="Timezone" value={campaign.timezone} /></dl>
                    </section>

                    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.45fr)_minmax(18rem,0.75fr)]">
                        <section className="content-card" aria-labelledby="readiness-heading">
                            <div className="mb-4 flex items-start justify-between gap-3"><div><h2 id="readiness-heading" className="text-lg font-semibold text-foreground">Server readiness</h2><p className="mt-1 text-sm text-muted-foreground">The server re-evaluates ownership, trunk health, policies, and configuration version at activation.</p></div><Button type="button" variant="ghost" size="icon" onClick={() => void readinessQuery.refetch()} disabled={readinessQuery.isFetching} aria-label="Refresh readiness"><RefreshCw className={readinessQuery.isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} aria-hidden /></Button></div>
                            {readinessQuery.isError ? <InboundErrorState title="Readiness could not be refreshed" message={readinessQuery.error instanceof Error ? readinessQuery.error.message : "Try again."} onRetry={() => void readinessQuery.refetch()} /> : readiness ? <InboundReadinessChecklist readiness={readiness} /> : <InboundLoadingState label="Checking activation readiness…" />}
                        </section>
                        <aside className="space-y-5">
                            <section className="content-card" aria-labelledby="safety-summary-heading"><h2 id="safety-summary-heading" className="text-lg font-semibold text-foreground">Safety policy</h2><dl className="mt-4 space-y-3"><Info label="After hours" value={afterHoursLabel(campaign.after_hours_action)} />{campaign.after_hours_action === "transfer" ? <Info label="Transfer destination" value={campaign.transfer_number ?? "Not configured"} /> : null}<Info label="Recording" value={campaign.recording_enabled ? "Enabled with disclosure" : "Disabled"} /></dl></section>
                            <section className="content-card" aria-labelledby="version-heading"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-primary" aria-hidden /><h2 id="version-heading" className="text-sm font-semibold text-foreground">Server state</h2></div><dl className="mt-4 space-y-3"><Info label="Updated" value={formatDate(campaign.updated_at)} /><Info label="Activated" value={formatDate(campaign.active_at)} /></dl></section>
                        </aside>
                    </div>

                    <div className="grid gap-5 xl:grid-cols-2">
                        <LiveCallsPanel
                            campaignId={campaign.campaign_id}
                            direction="inbound"
                            title="Live inbound calls"
                        />
                        <RejectedInboundCallsPanel campaignId={campaign.campaign_id} />
                    </div>
                    <CallIssuesPanel
                        campaignId={campaign.campaign_id}
                        direction="inbound"
                        title="Inbound call issues"
                    />
                    <KnowledgePanel campaignId={campaign.campaign_id} readOnly={!canManageKnowledge} />
                </div>
            )}

            <ConfirmDialog open={Boolean(action)} onOpenChange={(open) => !open && setAction(null)} intent={action === "archive" ? "delete" : "cancel"} title={actionCopy.title} description={actionCopy.description} warningText={actionCopy.warning} confirmLabel={actionCopy.label} pendingLabel={actionCopy.pending} confirmDisabled={action === "activate" && !canActivate} onConfirm={confirmAction} />
        </DashboardLayout>
    );
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div className="min-w-0"><dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt><dd className={`${mono ? "font-mono" : ""} mt-1 break-words text-sm font-medium capitalize text-foreground`}>{value}</dd></div>; }
function afterHoursLabel(action: string): string {
    if (action === "hangup") return "Reject before answer";
    if (action === "voicemail") return "AI message intake (normal call history; readiness required)";
    return "Transfer (activation blocked)";
}
function formatDate(value?: string | null): string { if (!value) return "Unknown"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "Unknown" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date); }
