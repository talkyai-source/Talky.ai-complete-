"use client";

import { Clock, Phone, PhoneForwarded, PhoneIncoming, Receipt, Route, RefreshCw, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { transferLegTerminalReason, type TransferLeg } from "@/lib/dashboard-api";
import {
    billingHoldReasonLabel,
    billingStatusLabel,
    statusPillClass,
    transferLegStatusLabel,
} from "@/lib/status-colors";

// Presentational pieces shared by the call-history list (`/calls`) and the
// call detail page (`/calls/[id]`).
//
// They live here rather than in either page because a Next.js route file may
// carry no named exports beyond the route-config keys — the generated check at
// `.next/types/app/calls/page.ts` rejects anything else — so a component
// defined in a page cannot be imported by a test.

function formatDuration(seconds?: number) {
    if (!seconds) return "--";
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, "0")}`;
}

/**
 * A failed load on the call detail page, with the control that re-runs it.
 *
 * The query layer does not auto-retry these, so without an explicit control
 * the only recovery a user has is a full page reload — which also discards
 * every other panel that loaded fine.
 */
export function CallLoadError({ message, onRetry }: { message: string; onRetry?: () => void }) {
    return (
        <div role="alert" className="content-card flex flex-wrap items-center gap-3 border-destructive/30 text-destructive">
            <span className="text-sm">{message}</span>
            {onRetry ? (
                <Button type="button" variant="outline" size="sm" onClick={onRetry}>
                    <RefreshCw className="h-4 w-4" aria-hidden />
                    Try again
                </Button>
            ) : null}
        </div>
    );
}

/**
 * A failed load on the call-history screen, with the control that re-runs it.
 *
 * These queries do not auto-retry, so without an explicit control the only
 * recovery is a full page reload.
 */
export function CallsLoadError({ message, onRetry }: { message: string; onRetry?: () => void }) {
    return (
        <div role="alert" className="content-card flex flex-wrap items-center gap-3 border-destructive/30 text-destructive">
            <span className="text-sm">{message}</span>
            {onRetry ? (
                <button
                    type="button"
                    onClick={onRetry}
                    className="inline-flex items-center gap-1.5 rounded-md border border-destructive/40 px-2.5 py-1 text-xs font-semibold text-destructive transition-colors hover:bg-destructive/10"
                >
                    <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                    Try again
                </button>
            ) : null}
        </div>
    );
}

/**
 * Transfer legs of this call, as the server projected them.
 *
 * Renders nothing at all when the server sent no legs — which is every call
 * that was never transferred, and every call at all while the controlled
 * transfer runtime is closed. This adds no gate of its own and lifts none:
 * it shows exactly the rows `GET /calls/{call_id}` returned, and each field
 * only when that field is present.
 *   backend/app/api/v1/endpoints/calls.py:265, :1328-1372
 */
export function TransferLegsPanel({ legs }: { legs?: TransferLeg[] | null }) {
    if (!legs || legs.length === 0) return null;
    return (
        <section className="content-card" aria-labelledby="transfer-legs-heading">
            <h2 id="transfer-legs-heading" className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground">
                <PhoneForwarded className="h-4 w-4 text-primary" aria-hidden />
                Transfer legs
            </h2>
            <ul className="space-y-3">
                {legs.map((leg, index) => {
                    const status = transferLegStatusLabel(leg.status);
                    const reason = transferLegTerminalReason(leg);
                    return (
                        <li key={leg.id || leg.provider_leg_id || index} className="rounded-xl border border-border bg-muted/40 p-3">
                            <div className="flex flex-wrap items-center gap-2">
                                {status ? (
                                    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${statusPillClass(leg.status)}`}>
                                        {status}
                                    </span>
                                ) : null}
                                {leg.to_number ? (
                                    <span className="font-mono text-xs text-foreground">{leg.to_number}</span>
                                ) : null}
                                {typeof leg.duration_seconds === "number" ? (
                                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                        <Clock className="h-3.5 w-3.5" aria-hidden />
                                        {formatDuration(leg.duration_seconds)}
                                    </span>
                                ) : null}
                                {leg.answered_at ? (
                                    <time dateTime={leg.answered_at} className="text-xs text-muted-foreground">
                                        Answered {new Date(leg.answered_at).toLocaleString()}
                                    </time>
                                ) : null}
                                {leg.ended_at ? (
                                    <time dateTime={leg.ended_at} className="text-xs text-muted-foreground">
                                        Ended {new Date(leg.ended_at).toLocaleString()}
                                    </time>
                                ) : null}
                            </div>
                            {reason ? (
                                <p className="mt-2 text-xs text-muted-foreground">{reason}</p>
                            ) : null}
                        </li>
                    );
                })}
            </ul>
        </section>
    );
}

/**
 * What the server did with the money for this call.
 *
 * Every value is rendered exactly as `GET /calls/{call_id}` sent it. Nothing
 * here is computed: the billed quantity is the ledger figure the server
 * projected (`backend/app/api/v1/endpoints/calls.py:1306-1312`), shown in the
 * unit the wire declares — seconds — and never converted into minutes or into
 * money. A call the server has not settled has no billed quantity, so this
 * renders no figure for it rather than falling back to its duration.
 *
 * The whole panel disappears when the server sent none of the three fields,
 * which is every outbound call and every inbound call on an older server.
 *
 * There is deliberately no charged-amount row: `calls.cost` is projected
 * (`calls.py:1427`) but the response carries no currency alongside it, and
 * inbound settlements are recorded with `amount`/`currency` NULL against
 * `cost_authority: carrier_cdr_unavailable`
 * (`backend/app/domain/services/telephony/inbound_transfer.py:546-566`).
 * Rendering a bare number as an amount would invent a unit the server never
 * declared.
 */
export function CallBillingPanel({
    billingStatus,
    billingHoldReason,
    billedDurationSeconds,
}: {
    billingStatus?: string | null;
    billingHoldReason?: string | null;
    billedDurationSeconds?: number | null;
}) {
    const status = (billingStatus || "").trim();
    const holdReason = (billingHoldReason || "").trim();
    const hasBilledDuration = typeof billedDurationSeconds === "number";
    if (!status && !holdReason && !hasBilledDuration) return null;

    return (
        <section className="content-card" aria-labelledby="call-billing-heading">
            <h2 id="call-billing-heading" className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground">
                <Receipt className="h-4 w-4 text-primary" aria-hidden />
                Billing
            </h2>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                {status ? (
                    <div className="flex items-center justify-between gap-3">
                        <dt className="text-xs text-muted-foreground">Status</dt>
                        <dd>
                            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${statusPillClass(status)}`}>
                                {billingStatusLabel(status)}
                            </span>
                        </dd>
                    </div>
                ) : null}
                {hasBilledDuration ? (
                    <div className="flex items-center justify-between gap-3">
                        <dt className="text-xs text-muted-foreground">Billed duration</dt>
                        <dd className="text-sm font-semibold text-foreground tabular-nums">
                            {billedDurationSeconds.toLocaleString()}
                            <span className="ml-1 text-xs font-normal text-muted-foreground">seconds</span>
                        </dd>
                    </div>
                ) : null}
            </dl>
            {holdReason ? (
                <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 p-2 text-xs text-muted-foreground">
                    {billingHoldReasonLabel(holdReason)}
                </p>
            ) : null}
        </section>
    );
}

/**
 * Distinct DIDs seen on the currently loaded page of calls, for the DID
 * filter on `/calls`. Client-side over what is already on the wire — the
 * same source `to_number` renders from elsewhere on this page — so this adds
 * no request of its own. Calls with no DID (every outbound call) contribute
 * no option.
 */
export function distinctDidOptions(calls: Array<{ to_number?: string | null }>): string[] {
    const dids = new Set<string>();
    for (const call of calls) {
        if (call.to_number) dids.add(call.to_number);
    }
    return Array.from(dids).sort();
}

/**
 * Inbound campaigns present on the currently loaded page of calls, for the
 * campaign filter on `/calls`. Labeled by `campaign_name` exactly as the
 * server sent it; falls back to the campaign id itself rather than resolving
 * a name through any lookup the wire does not already carry. A call with no
 * `inbound_campaign_id` contributes no option — there is nothing to filter
 * or link to.
 */
export function inboundCampaignFilterOptions(calls: Array<{ inbound_campaign_id?: string | null; campaign_name?: string }>): Array<{ id: string; name: string }> {
    const byId = new Map<string, string>();
    for (const call of calls) {
        const id = call.inbound_campaign_id;
        if (!id) continue;
        if (!byId.has(id)) byId.set(id, call.campaign_name || id);
    }
    return Array.from(byId, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}

/** A metadata row: label plus value, "Unknown" when the server sent none. */
export function Metadata({ label, value }: { label: string; value: string | number | null | undefined }) {
    return (
        <div className="rounded-xl border border-border bg-muted/40 px-3 py-2">
            <dt className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">{label}</dt>
            <dd className="mt-1 break-all font-mono text-xs text-foreground">{value === null || value === undefined || value === "" ? "Unknown" : String(value)}</dd>
        </div>
    );
}

/**
 * Who was on the line, exactly as the server projected it — never gated on a
 * capability, because caller and DID are ordinary call identity, not a
 * routing or consent detail.
 *   backend/app/api/v1/endpoints/calls.py:183 (from_number), :201-202 (caller_ani/called_did)
 */
export function CallPartiesPanel({
    direction,
    phoneNumber,
    toNumber,
}: {
    direction?: "inbound" | "outbound";
    phoneNumber?: string;
    toNumber?: string | null;
}) {
    const inbound = direction === "inbound";
    return (
        <>
            <div className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm transition-[transform,background-color,border-color,box-shadow] duration-150 ease-out hover:-translate-y-0.5 hover:bg-background hover:shadow-md">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground transition-colors group-hover:bg-background">
                    <Phone className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{inbound ? "Caller ANI" : "Phone Number"}</div>
                    <div className="mt-0.5 truncate text-sm font-semibold text-foreground">{phoneNumber}</div>
                </div>
            </div>

            {inbound ? (
                <div className="group flex items-center gap-3 rounded-2xl border border-border bg-muted/60 p-3 shadow-sm">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-background/60 text-foreground"><PhoneIncoming className="h-5 w-5" /></div>
                    <div className="min-w-0 flex-1"><div className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">Called DID</div><div className="mt-0.5 truncate text-sm font-semibold text-foreground">{toNumber || "Unavailable"}</div></div>
                </div>
            ) : null}
        </>
    );
}

/**
 * Assignment, route and config identity for an inbound call — the routing
 * detail that mirrors what `/inbound-campaigns` already gates behind
 * `inbound:read`/`inbound:manage`. Renders nothing without that capability;
 * the caller must still check `call.direction === "inbound"` before mounting
 * this, since an outbound call has none of these fields.
 *
 * The "Inbound campaign" row shows the id the wire carries
 * (`inbound_campaign_id` — calls.py:203) — there is no separate inbound
 * campaign display-name field on the contract, so nothing here resolves one.
 * The link renders only when that id is present.
 */
export function InboundRouteSnapshot({
    canView,
    inboundCampaignId,
    assignmentId,
    routeId,
    routeVersion,
    configVersion,
    configChecksum,
}: {
    canView: boolean;
    inboundCampaignId?: string | null;
    assignmentId?: string | null;
    routeId?: string | null;
    routeVersion?: number | null;
    configVersion?: number | null;
    configChecksum?: string | null;
}) {
    if (!canView) return null;
    const campaignId = typeof inboundCampaignId === "string" ? inboundCampaignId.trim() : "";
    return (
        <section className="content-card" aria-labelledby="inbound-route-snapshot-heading">
            <h2 id="inbound-route-snapshot-heading" className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground"><Route className="h-4 w-4 text-primary" aria-hidden />Inbound route snapshot</h2>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
                <Metadata label="Inbound campaign" value={campaignId || undefined} />
                <Metadata label="Assignment" value={assignmentId} />
                <Metadata label="Route" value={routeId} />
                <Metadata label="Route version" value={routeVersion} />
                <Metadata label="Config version" value={configVersion} />
                <Metadata label="Config checksum" value={configChecksum ? `${configChecksum.slice(0, 12)}…` : undefined} />
            </dl>
            {campaignId ? <Button asChild variant="outline" size="sm" className="mt-4"><Link href={`/inbound-campaigns/${encodeURIComponent(campaignId)}`}>Open inbound campaign</Link></Button> : null}
        </section>
    );
}

/**
 * Consent and media pipeline state for an inbound call — gated the same as
 * {@link InboundRouteSnapshot}. Renders nothing at all when the capability is
 * missing, or when the server sent none of the six state fields — the
 * ordinary case for an outbound call.
 */
export function InboundConsentStatePanel({
    canView,
    admissionStatus,
    consentStatus,
    processingStatus,
    mediaState,
    recordingStatus,
    transcriptStatus,
    admissionReason,
}: {
    canView: boolean;
    admissionStatus?: string | null;
    consentStatus?: string | null;
    processingStatus?: string | null;
    mediaState?: string | null;
    recordingStatus?: string | null;
    transcriptStatus?: string | null;
    admissionReason?: string | null;
}) {
    if (!canView) return null;
    if (!admissionStatus && !consentStatus && !processingStatus && !mediaState && !recordingStatus && !transcriptStatus) return null;
    return (
        <section className="content-card" aria-labelledby="inbound-consent-state-heading">
            <h2 id="inbound-consent-state-heading" className="mb-4 flex items-center gap-2 text-sm font-semibold text-foreground"><ShieldCheck className="h-4 w-4 text-primary" aria-hidden />Consent and media state</h2>
            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1"><Metadata label="Admission" value={admissionStatus} /><Metadata label="Consent" value={consentStatus} /><Metadata label="Processing" value={processingStatus} /><Metadata label="Media" value={mediaState} /><Metadata label="Recording" value={recordingStatus} /><Metadata label="Transcript" value={transcriptStatus} /></dl>
            {admissionReason ? <p className="mt-3 rounded-lg border border-amber-500/25 bg-amber-500/5 p-2 text-xs text-muted-foreground">{admissionReason}</p> : null}
        </section>
    );
}
