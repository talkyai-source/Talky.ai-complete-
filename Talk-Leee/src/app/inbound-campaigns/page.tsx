"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowRight, PhoneIncoming, Plus, Search, ShieldAlert } from "lucide-react";

import { InboundEmptyState, InboundErrorState, InboundLoadingState, InboundPermissionState } from "@/components/inbound/inbound-page-state";
import { InboundStatusBadge, ReadinessBadge } from "@/components/inbound/inbound-status";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/useAuth";
import { type InboundCampaign, type InboundLifecycleStatus } from "@/lib/inbound-api";
import { getInboundCapabilities } from "@/lib/inbound-permissions";
import { useEffectivePermissions, useInboundCampaigns, useSetTenantInboundControls, useTenantInboundControls } from "@/lib/queries/inbound-queries";
import { cn } from "@/lib/utils";

export default function InboundCampaignsPage() {
    const { user } = useAuth();
    const permissions = useEffectivePermissions();
    const [search, setSearch] = useState("");
    const [status, setStatus] = useState<"all" | InboundLifecycleStatus>("all");
    const [controlDialogOpen, setControlDialogOpen] = useState(false);
    const [controlReason, setControlReason] = useState("");
    const capabilities = getInboundCapabilities(user?.role, permissions.isSuccess ? permissions.data.permissions : undefined);
    const query = useInboundCampaigns(
        permissions.isSuccess && capabilities.canView,
        status === "archived",
    );
    const tenantControls = useTenantInboundControls(permissions.isSuccess && capabilities.canChangeControls);
    const setTenantControls = useSetTenantInboundControls();
    const nextInboundEnabled = !(tenantControls.data?.inbound_enabled ?? false);

    const visible = useMemo(() => {
        const needle = search.trim().toLowerCase();
        return (query.data ?? []).filter((campaign) => {
            if (status !== "all" && campaign.status !== status) return false;
            if (!needle) return true;
            return [campaign.name, campaign.campaign_name, campaign.phone_number?.masked_number, campaign.sip_trunk_name]
                .some((value) => value?.toLowerCase().includes(needle));
        });
    }, [query.data, search, status]);

    return (
        <DashboardLayout title="Inbound Campaigns" description="Verified number routing, AI behavior, readiness, and lifecycle control">
            {permissions.isLoading ? <InboundLoadingState label="Checking inbound permissions…" /> : permissions.isError ? (
                <InboundErrorState title="Permissions could not be verified" message="Inbound data and routing controls remain unavailable until server permissions can be confirmed." onRetry={() => void permissions.refetch()} />
            ) : !capabilities.canView ? <InboundPermissionState /> : (
                <div className="space-y-5">
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex items-start gap-3 rounded-xl border border-border bg-muted/20 p-3 text-sm text-muted-foreground">
                            <PhoneIncoming className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                            <span>Draft changes are durable. Only server-confirmed activation updates live call routing.</span>
                        </div>
                        {capabilities.canCreate ? <Button asChild className="shrink-0"><Link href="/inbound-campaigns/new"><Plus className="h-4 w-4" aria-hidden />New inbound campaign</Link></Button> : null}
                    </div>

                    {capabilities.canChangeControls ? (
                        <section className="content-card" aria-labelledby="tenant-inbound-control-heading">
                            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                                <div className="flex min-w-0 items-start gap-3">
                                    <span className={cn("mt-0.5 rounded-lg p-2", tenantControls.data?.inbound_enabled ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/10 text-amber-700 dark:text-amber-300")}><ShieldAlert className="h-4 w-4" aria-hidden /></span>
                                    <div>
                                        <h2 id="tenant-inbound-control-heading" className="text-sm font-semibold text-foreground">Tenant inbound admission</h2>
                                        {tenantControls.isLoading ? <p className="mt-1 text-sm text-muted-foreground">Loading the authoritative switch…</p> : tenantControls.isError ? <p className="mt-1 text-sm text-destructive" role="alert">The switch could not be verified, so it cannot be changed.</p> : (
                                            <>
                                                <p className="mt-1 text-sm text-muted-foreground">{tenantControls.data?.inbound_enabled ? "Enabled. Campaign readiness still applies to every new call." : "Disabled. New inbound calls fail closed before answer."}</p>
                                                {tenantControls.data?.reason ? <p className="mt-1 text-xs text-muted-foreground">Last reason: {tenantControls.data.reason}</p> : null}
                                            </>
                                        )}
                                    </div>
                                </div>
                                <Button type="button" variant={tenantControls.data?.inbound_enabled ? "destructive" : "outline"} disabled={!tenantControls.data || setTenantControls.isPending} onClick={() => setControlDialogOpen(true)}>
                                    {tenantControls.data?.inbound_enabled ? "Disable all inbound" : "Enable inbound"}
                                </Button>
                            </div>
                        </section>
                    ) : null}

                    <div className="content-card flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                        <div className="relative w-full md:max-w-sm"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden /><Input value={search} onChange={(event) => setSearch(event.target.value)} className="pl-9" type="search" aria-label="Search inbound campaigns" placeholder="Search campaign, number, or trunk" /></div>
                        <div className="flex max-w-full gap-1 overflow-x-auto pb-1" role="group" aria-label="Filter by lifecycle status">
                            {(["all", "draft", "active", "paused", "archived"] as const).map((item) => <button key={item} type="button" onClick={() => setStatus(item)} aria-pressed={status === item} className={cn("rounded-lg px-3 py-2 text-xs font-medium capitalize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", status === item ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:text-foreground")}>{item}</button>)}
                        </div>
                    </div>

                    {query.isLoading ? <InboundLoadingState label="Loading inbound campaigns…" /> : query.isError ? <InboundErrorState message={query.error instanceof Error ? query.error.message : "The campaign list could not be loaded."} onRetry={() => void query.refetch()} /> : (query.data ?? []).length === 0 ? <InboundEmptyState onCreate={capabilities.canCreate ? <Button asChild><Link href="/inbound-campaigns/new">Create the first draft</Link></Button> : undefined} /> : visible.length === 0 ? (
                        <div className="content-card py-14 text-center"><h2 className="text-base font-semibold text-foreground">No campaigns match</h2><p className="mt-1 text-sm text-muted-foreground">Clear the search or choose a different status.</p><Button type="button" variant="outline" className="mt-4" onClick={() => { setSearch(""); setStatus("all"); }}>Clear filters</Button></div>
                    ) : (
                        <div className="grid gap-4 lg:grid-cols-2">
                            {visible.map((campaign) => <CampaignCard key={campaign.id} campaign={campaign} canEdit={capabilities.canEdit} />)}
                        </div>
                    )}
                </div>
            )}
            <ConfirmDialog
                open={controlDialogOpen}
                onOpenChange={(open) => { setControlDialogOpen(open); if (!open) setControlReason(""); }}
                intent={nextInboundEnabled ? "reactivate" : "suspend"}
                title={nextInboundEnabled ? "Enable tenant inbound calling?" : "Disable all tenant inbound calling?"}
                description={nextInboundEnabled ? "This restores the tenant admission gate; campaign readiness remains enforced." : "This is the tenant emergency stop for every inbound campaign."}
                warningText={nextInboundEnabled ? "Only individually active and ready campaigns will answer new calls." : "New calls will fail closed before answer. Calls already in progress are not interrupted."}
                confirmLabel={nextInboundEnabled ? "Enable inbound" : "Disable all inbound"}
                pendingLabel={nextInboundEnabled ? "Enabling…" : "Disabling…"}
                showReasonInput
                reasonValue={controlReason}
                onReasonChange={setControlReason}
                confirmDisabled={controlReason.trim().length < 3 || !tenantControls.data}
                onConfirm={async () => {
                    if (!tenantControls.data) throw new Error("The authoritative control version is unavailable.");
                    await setTenantControls.mutateAsync({
                        inbound_enabled: nextInboundEnabled,
                        expected_version: tenantControls.data.version,
                        reason: controlReason.trim(),
                    });
                }}
            />
        </DashboardLayout>
    );
}

function CampaignCard({ campaign, canEdit }: { campaign: InboundCampaign; canEdit: boolean }) {
    return (
        <article className="content-card flex min-w-0 flex-col gap-4">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><h2 className="truncate text-lg font-semibold text-foreground">{campaign.name}</h2><p className="mt-1 font-mono text-sm text-muted-foreground">{campaign.phone_number?.masked_number ?? "No verified number assigned"}</p></div>
                <InboundStatusBadge status={campaign.status} />
            </div>
            <div className="flex flex-wrap items-center gap-2"><ReadinessBadge readiness={campaign.readiness} /><span className="rounded-full border border-border bg-muted/30 px-2.5 py-1 text-xs text-muted-foreground">Config v{campaign.config_version}</span></div>
            <dl className="grid gap-3 text-sm sm:grid-cols-2"><Info label="AI campaign" value={campaign.campaign_name || campaign.campaign_id || "Not selected"} /><Info label="Inbound trunk" value={campaign.sip_trunk_name || campaign.sip_trunk_id || "Not selected"} /><Info label="Timezone" value={campaign.timezone} /><Info label="Last call" value={formatDate(campaign.last_call_at)} /></dl>
            {campaign.last_error ? <p className="rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2 text-xs text-destructive" role="status">{campaign.last_error}</p> : null}
            <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button asChild size="sm" variant="outline"><Link href={`/inbound-campaigns/${campaign.id}`}>Open <ArrowRight className="h-4 w-4" aria-hidden /></Link></Button>
                {canEdit && campaign.status !== "archived" ? <Button asChild size="sm" variant="ghost"><Link href={`/inbound-campaigns/${campaign.id}/edit`}>Edit</Link></Button> : null}
            </div>
        </article>
    );
}

function Info({ label, value }: { label: string; value: string }) { return <div className="min-w-0"><dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</dt><dd className="mt-1 truncate text-foreground" title={value}>{value}</dd></div>; }
function formatDate(value?: string | null): string { if (!value) return "No calls yet"; const date = new Date(value); return Number.isNaN(date.getTime()) ? "Unknown" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date); }
