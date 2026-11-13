"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState, ErrorState, LoadingState, failedReadMessage } from "@/components/states/page-states";
import { dashboardApi, Campaign, Contact, ContactList, MinutesStatus } from "@/lib/dashboard-api";
import type { CampaignActivity, CampaignBlockingReason, ContactMutation } from "@/lib/dashboard-api";
import { OPTIONAL_CONTACT_FIELDS, contactPayload, contactEditPayload, fetchAllPages, type OptionalContactFieldKey } from "@/lib/contact-fields";
import { PHONE_HELP_TEXT, parsePhoneNumber, type CountrySelection } from "@/lib/phone-number";
import { DefaultCountrySelect } from "@/components/contacts/default-country-select";
import { PhoneStatusLine } from "@/components/contacts/phone-status-line";
import { SmartCsvImport } from "@/components/campaigns/smart-csv-import";
import { ContactLists, ActiveContactsSummary } from "@/components/campaigns/contact-lists";
import { ScriptCard } from "@/components/campaigns/script-card";
import { LiveCallsPanel } from "@/components/campaigns/live-calls-panel";
import { CallIssuesPanel } from "@/components/campaigns/call-issues-panel";
import { ContactCapturedDetails } from "@/components/calls/lead-details-panel";
import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { TestAgentButton } from "@/components/campaigns/test-agent-button";
import { CampaignReadinessNotice, useCampaignReadiness } from "@/components/campaigns/campaign-readiness";
import { CallingScheduleLine } from "@/components/campaigns/calling-schedule-notice";
import { CampaignStartDialog, useCampaignStartDialog } from "@/components/campaigns/campaign-start-dialog";
import { CampaignControlNoticeBox } from "@/components/campaigns/campaign-control-notice";
import {
    campaignStartLabel,
    canStartCampaign,
    contactsQueuedText,
    outOfMinutesText,
    runCampaignPause,
    runCampaignStop,
    useCampaignsInFlight,
    type CampaignControlNotice,
} from "@/lib/campaign-control";
import { campaignDisplayStatus } from "@/lib/campaign-status";
import { CampaignDuplicateStatus, duplicateResultNotice, useCampaignDuplicate } from "@/components/campaigns/campaign-duplicate";
import { stashDuplicateNotice } from "@/lib/campaign-duplicate";
import { humanizeOutcome } from "@/lib/call-outcome-label";
import { inboundCampaignHrefForBase, isOutboundCampaign } from "@/lib/campaign-direction";
import { inboundApi } from "@/lib/inbound-api";
import { useEffectivePermissions } from "@/lib/queries/inbound-queries";
import {
    ArrowLeft,
    Copy,
    Play,
    Pause,
    Square,
    Plus,
    Upload,
    Pencil,
    Users,
    Phone,
    CheckCircle,
    XCircle,
    Loader2,
    Trash2,
    Search,
    Clock,
    AlertTriangle,
} from "lucide-react";
import { motion } from "framer-motion";

function getStatusStyle(status: string) {
    switch (status) {
        case "running":
            return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20";
        case "paused":
            return "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border border-yellow-500/20";
        case "completed":
            return "bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-500/20";
        case "stopped":
            return "bg-red-500/10 text-red-700 dark:text-red-400 border border-red-500/20";
        default:
            return "bg-muted text-muted-foreground border border-border";
    }
}

// Normal pacing between calls (mirrors the backend's SELF_CLEARING_CODES): the
// campaign IS dialing, so these never earn a "Not dialing" banner.
const PACING_BLOCK_CODES = new Set([
    "batch_capacity", "call_gap", "tenant_gap", "call_guard_throttled", "call_guard_queued",
]);

// Reasons that stop the WHOLE campaign. The block state is per campaign and
// last-writer-wins, so a per-contact reason (one number's cooldown, DNC, daily
// cap or guard block) must not read as "the campaign is not dialing".
const CAMPAIGN_LEVEL_BLOCK_CODES = new Set([
    "schedule_day_not_allowed", "schedule_outside_window", "out_of_minutes", "metering_unavailable",
    "max_concurrent_calls", "voice_pipeline_unavailable", "caller_id_not_verified", "line_not_ready",
    "telephony_unavailable", "testing_mode_schedule_bypassed",
]);

type DialBlock = { message: string; testing: boolean; error: boolean; nextEligibleAt: string | null };

/** Why a RUNNING campaign is not placing calls, from the stats the page polls. */
function dialBlockFor(
    status: string | undefined,
    reason: CampaignBlockingReason | null | undefined,
    activity: CampaignActivity | null | undefined,
    minutesExhausted?: boolean,
): DialBlock | null {
    if (status !== "running" && status !== "active") return null;
    const code = reason?.code ?? activity?.reason_code ?? null;
    if (code && PACING_BLOCK_CODES.has(code)) return null;
    // Left over from before a Start/Resume: the block state is only cleared
    // by the next placed call, so trust the campaign status instead.
    if (code === "campaign_not_running") return null;
    // Stale after a top-up: the page's own minutes status is fresher.
    if (code === "out_of_minutes" && minutesExhausted === false) return null;
    const waiting = activity?.waiting_on_schedule === true || activity?.state === "waiting_for_calling_window";
    if (code && !waiting && !CAMPAIGN_LEVEL_BLOCK_CODES.has(code)) return null;
    const message = reason?.message?.trim() || (waiting ? "Waiting for the calling window to open." : code);
    if (!message) return null;
    return {
        message,
        testing: code === "testing_mode_schedule_bypassed",
        error: reason?.severity === "error",
        nextEligibleAt: reason?.next_eligible_at ?? activity?.next_eligible_at ?? null,
    };
}

function fmtNextAttempt(iso: string | null): string {
    const t = iso ? Date.parse(iso) : NaN;
    if (Number.isNaN(t)) return "";
    return new Date(t).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function getContactStatusStyle(result: string) {
    switch (result) {
        case "goal_achieved":
            return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20";
        case "pending":
            return "bg-muted text-muted-foreground border border-border";
        default:
            return "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border border-yellow-500/20";
    }
}

export default function CampaignDetailPage() {
    const params = useParams();
    const campaignId = params.id as string;

    return <CampaignDetailScope key={campaignId} campaignId={campaignId} />;
}

function CampaignDetailScope({ campaignId }: { campaignId: string }) {
    const router = useRouter();
    const permissions = useEffectivePermissions();
    const canManageKnowledge = permissions.isSuccess && (
        permissions.data.role?.toLowerCase() === "platform_admin"
        || permissions.data.permissions.includes("campaigns:update")
    );

    const [campaign, setCampaign] = useState<Campaign | null>(null);
    const [contacts, setContacts] = useState<Contact[]>([]);
    const [stats, setStats] = useState<{
        total_leads: number;
        qualified_leads?: number;
        goals_achieved: number;
        job_status_counts: Record<string, number>;
        call_outcome_counts: Record<string, number>;
        blocking_reason?: CampaignBlockingReason | null;
        activity?: CampaignActivity | null;
    } | null>(null);
    const [loading, setLoading] = useState(true);
    // A Pause or Stop of this page is running (request + the reload after it).
    const [controlBusy, setControlBusy] = useState(false);
    // The shared in-flight sets: a start / stop / pause of this campaign that is
    // running from any entry point (this page or the campaign list).
    const inFlight = useCampaignsInFlight();
    const starting = inFlight.starting.has(campaignId);
    const stopping = inFlight.stopping.has(campaignId);
    const pausing = inFlight.pausing.has(campaignId);
    const actionLoading = controlBusy || starting || stopping || pausing;
    // Result of the last Start / Pause / Stop, shown inline (never an alert()).
    const [controlNotice, setControlNotice] = useState<CampaignControlNotice | null>(null);
    const [error, setError] = useState("");
    const [minutes, setMinutes] = useState<MinutesStatus | null>(null);
    const loadGeneration = useRef(0);
    const scopeActive = useRef(true);
    // Why the last Start attempt was refused, in the server's own words.
    // Empty until something is actually refused; never pre-populated.
    const [startError, setStartError] = useState("");
    const canStart = Boolean(campaign && canStartCampaign(campaign.status));
    const readiness = useCampaignReadiness([campaignId], canStart);
    // Duplicate (3.1): the same shared copy routine and rules as the campaign
    // list. Once the copy exists its page opens, carrying the result message.
    const duplicate = useCampaignDuplicate(campaign ? campaignId : null, (result) => {
        stashDuplicateNotice(result.campaign.id, duplicateResultNotice(result));
        router.push(`/campaigns/${result.campaign.id}`);
    });

    // Out of plan minutes ⇒ the backend will 402 a Start, so we disable the
    // button up front and explain why. `unlimited` plans are never blocked.
    const outOfMinutes = !!minutes && !minutes.unlimited && minutes.exhausted;
    const dialBlock = dialBlockFor(
        campaign?.status, stats?.blocking_reason, stats?.activity,
        minutes ? outOfMinutes : undefined,
    );

    // CSV Upload (smart import modal)
    const [csvModalOpen, setCsvModalOpen] = useState(false);

    // Contact lists — the grouped-upload cards + the active-contacts summary.
    // `listsRefreshToken` re-fetches the lists row after an import; `lists`
    // mirrors what the row loaded so the Start summary can read active counts.
    const [listsRefreshToken, setListsRefreshToken] = useState(0);
    const [lists, setLists] = useState<ContactList[]>([]);
    const listsSectionRef = useRef<HTMLDivElement | null>(null);

    // Add contact form
    const [showAddContact, setShowAddContact] = useState(false);
    const [contactForm, setContactForm] = useState<ContactMutation>({
        phone_number: "",
        first_name: "",
        last_name: "",
        email: "",
    });
    // Optional contact details the user chooses to add from a dropdown (the
    // backend already accepts every one of these on POST/PATCH contacts).
    const [extraContactFields, setExtraContactFields] = useState<OptionalContactFieldKey[]>([]);
    const [addingContact, setAddingContact] = useState(false);
    // "Default country" for the Add/Edit contact form (EP4): starts empty,
    // applies only to numbers without "+", never stored between visits.
    const [contactCountry, setContactCountry] = useState<CountrySelection>("");
    // What each number will be saved as (or why it cannot be), from the ONE
    // shared phone module; Save stays blocked while any is invalid.
    const phoneStatus = parsePhoneNumber(contactForm.phone_number, contactCountry);
    const optionalNumberStatus = (key: OptionalContactFieldKey) => {
        if (key !== "mobile_number" && key !== "business_number") return null;
        const raw = String(contactForm[key] ?? "");
        return raw.trim() ? parsePhoneNumber(raw, contactCountry) : null;
    };
    const contactFormValid = phoneStatus.ok
        && extraContactFields.every((key) => optionalNumberStatus(key)?.ok ?? true);
    const [deletingContactId, setDeletingContactId] = useState<string | null>(null);
    // A failed add / edit, shown in the form it came from.
    const [contactFormError, setContactFormError] = useState("");
    // The contact the Remove contact dialog is asking about.
    const [removeTarget, setRemoveTarget] = useState<{ id: string; phone: string } | null>(null);
    // A failed delete, shown in the row it came from.
    const [contactDeleteError, setContactDeleteError] = useState<{ id: string; message: string } | null>(null);
    const [editingContact, setEditingContact] = useState<Contact | null>(null);
    const [contactSearch, setContactSearch] = useState("");
    const [contactFilter, setContactFilter] = useState<"all" | "leads">("all");

    const filteredContacts = contacts.filter((c) => {
        if (contactFilter === "leads" && !c.is_lead) return false;
        const term = contactSearch.trim().toLowerCase();
        if (!term) return true;
        const name = `${c.first_name || ""} ${c.last_name || ""}`.toLowerCase();
        return (
            (c.phone_number || "").toLowerCase().includes(term) ||
            name.includes(term) ||
            (c.email || "").toLowerCase().includes(term)
        );
    });

    const loadData = useCallback(async () => {
        const generation = ++loadGeneration.current;
        const superseded = () => !scopeActive.current || generation !== loadGeneration.current;
        try {
            setLoading(true);
            const campaignData = await dashboardApi.getCampaign(campaignId);
            if (superseded()) return;
            if (!isOutboundCampaign(campaignData.campaign)) {
                const inboundCampaigns = await inboundApi.list({ includeArchived: true });
                if (superseded()) return;
                router.replace(
                    inboundCampaignHrefForBase(campaignId, inboundCampaigns),
                );
                return;
            }

            const [contactsData, statsData, minutesData] = await Promise.all([
                fetchAllPages((page, size) => dashboardApi.listContacts(campaignId, page, size)),
                dashboardApi.getCampaignStats(campaignId),
                // Best-effort — a quota hiccup must not blank the whole page.
                dashboardApi.getMinutesStatus().catch(() => null),
            ]);
            if (superseded()) return;
            setError("");
            setCampaign(campaignData.campaign);
            setContacts(contactsData.items);
            setStats(statsData);
            setMinutes(minutesData);
        } catch (err) {
            if (superseded()) return;
            setError(failedReadMessage(err, "Failed to load campaign"));
        } finally {
            if (!superseded()) setLoading(false);
        }
    }, [campaignId, router]);

    useEffect(() => {
        scopeActive.current = true;
        return () => {
            scopeActive.current = false;
            loadGeneration.current += 1;
        };
    }, []);

    useEffect(() => {
        if (campaignId) {
            // eslint-disable-next-line react-hooks/set-state-in-effect -- fetches and stores campaign data when the route's campaignId changes
            void loadData();
        }
    }, [campaignId, loadData]);

    // Live-sync running campaigns and unused drafts. Draft polling is required
    // because a draft can be converted to inbound from another tab; without it
    // this outbound route keeps rendering controls for a campaign whose
    // lifecycle has changed. The poll remains lightweight (row + stats only).
    useEffect(() => {
        const status = campaign?.status;
        if (status !== "running" && status !== "active" && status !== "draft") return;
        let cancelled = false;
        const tick = async () => {
            try {
                const c = await dashboardApi.getCampaign(campaignId);
                if (cancelled) return;
                if (!isOutboundCampaign(c.campaign)) {
                    const inboundCampaigns = await inboundApi.list({ includeArchived: true });
                    if (cancelled) return;
                    router.replace(inboundCampaignHrefForBase(campaignId, inboundCampaigns));
                    return;
                }
                const s = await dashboardApi.getCampaignStats(campaignId).catch(() => null);
                if (cancelled) return;
                setCampaign(c.campaign);
                if (s) setStats(s);
                // A successful tick proves the backend is reachable again.
                setError("");
            } catch {
                /* transient network hiccup — the next tick retries */
            }
        };
        const id = window.setInterval(tick, 7000);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, [campaign?.status, campaignId, router]);

    // The ONE Start / Resume confirmation, shared with the campaign list
    // (components/campaigns/campaign-start-dialog). It closes once the start
    // request has answered; the campaign reads "Preparing" until the reload
    // below has brought the backend's own status.
    const startDialog = useCampaignStartDialog({
        minutes,
        onFinished: async (outcome) => {
            if (outcome.queued !== null) {
                setStartError("");
                setControlNotice({ kind: "success", lines: [contactsQueuedText(outcome.queued)] });
            } else if (outcome.notStarted.length > 0) {
                // Shown in the page rather than an alert(): the quota figures next
                // to it refresh underneath, so the reason stays readable beside the
                // numbers that explain it instead of vanishing on acknowledge.
                setStartError(outcome.notStarted[0]!.reason);
            }
            // Refresh status and quota so the button/banner reflect reality.
            await loadData();
        },
    });

    function handleStartClick() {
        if (!campaign || !readiness.ready) return;
        // Out of plan minutes — don't even open the modal; the backend
        // would 402 anyway. The banner above the button explains why.
        if (outOfMinutes) {
            setStartError(outOfMinutesText(minutes));
            return;
        }
        setStartError("");
        setControlNotice(null);
        startDialog.open([campaign]);
    }

    async function handlePause() {
        setControlBusy(true);
        setControlNotice(null);
        try {
            const notice = await runCampaignPause(campaignId, loadData);
            if (notice) setControlNotice(notice);
        } finally {
            setControlBusy(false);
        }
    }

    async function handleStop() {
        setControlBusy(true);
        setControlNotice(null);
        try {
            const notice = await runCampaignStop(campaignId, loadData);
            if (notice) setControlNotice(notice);
        } finally {
            setControlBusy(false);
        }
    }


    async function handleAddContact(e: React.FormEvent) {
        e.preventDefault();
        // An invalid number never becomes a contact: the button is disabled
        // and the submit is refused here too (the payload builders would throw).
        if (!contactFormValid) return;
        try {
            setAddingContact(true);
            setContactFormError("");
            const payload = editingContact
                ? contactEditPayload(contactForm, contactCountry)
                : contactPayload(contactForm, contactCountry);
            if (editingContact) {
                await dashboardApi.updateContact(campaignId, editingContact.id, payload);
            } else {
                await dashboardApi.addContact(campaignId, payload);
            }
            setContactForm({ phone_number: "", first_name: "", last_name: "", email: "" });
            setExtraContactFields([]);
            setShowAddContact(false);
            setEditingContact(null);
            await loadData();
        } catch (err) {
            setContactFormError(
                err instanceof Error
                    ? err.message
                    : `Failed to ${editingContact ? "update" : "add"} contact`,
            );
        } finally {
            setAddingContact(false);
        }
    }

    function startEditContact(contact: Contact) {
        setContactFormError("");
        setEditingContact(contact);
        const present = OPTIONAL_CONTACT_FIELDS.filter((f) => Boolean(contact[f.key])).map((f) => f.key);
        setExtraContactFields(present);
        setContactForm({
            phone_number: contact.phone_number || "",
            first_name: contact.first_name || "",
            last_name: contact.last_name || "",
            email: contact.email || "",
            ...Object.fromEntries(present.map((key) => [key, String(contact[key] ?? "")])),
        });
        setShowAddContact(true);
    }

    // Runs from the Remove contact dialog's confirm button. A failure is shown
    // in the contact's own row, so the dialog always closes.
    async function removeContact(contactId: string) {
        try {
            setContactDeleteError(null);
            setDeletingContactId(contactId);
            await dashboardApi.deleteContact(campaignId, contactId);
            // Optimistically drop the row; reload stats in the background.
            setContacts((prev) => prev.filter((c) => c.id !== contactId));
            loadData().catch(() => {});
        } catch (err) {
            setContactDeleteError({ id: contactId, message: err instanceof Error ? err.message : "Failed to delete contact" });
        } finally {
            setDeletingContactId(null);
        }
    }

    return (
        <DashboardLayout title={campaign?.name} description={campaign?.description ?? "Campaign stats, contacts, and controls."} breadcrumbLabels={campaign?.name ? { [campaignId]: campaign.name } : undefined}>
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
                <button
                    onClick={() => router.push("/campaigns")}
                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <ArrowLeft className="w-4 h-4" />
                    Back to campaigns
                </button>
            </motion.div>

            {/* Keep the last good data on screen during a reload or after a
                failed one: Pause/Stop must stay reachable while the campaign runs. */}
            {loading && !campaign ? (
                <LoadingState />
            ) : error && !campaign ? (
                <ErrorState message={error} onRetry={loadData} />
            ) : campaign ? (
                <div className="space-y-6">
                    {error ? (
                        <div role="alert" className="content-card border-red-500/30 bg-red-500/5 flex items-center justify-between gap-3">
                            <p className="text-sm text-red-400">Could not refresh this campaign: {error}</p>
                            <Button variant="outline" onClick={() => { void loadData(); }} disabled={loading}>
                                Retry
                            </Button>
                        </div>
                    ) : null}
                    {/* Header */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between"
                    >
                        <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-3">
                                <h2 className="break-words text-2xl font-semibold text-foreground">{campaign.name}</h2>
                                <span
                                    data-testid="campaign-status"
                                    className={`px-3 py-1 text-sm font-medium rounded-full ${getStatusStyle(starting || stopping ? "" : campaign.status)}`}
                                >
                                    {campaignDisplayStatus(campaign.status, { starting, stopping })}
                                </span>
                            </div>
                            {campaign.description && (
                                <p className="mt-2 text-muted-foreground">{campaign.description}</p>
                            )}
                            <CallingScheduleLine
                                callingConfig={campaign.calling_config}
                                testId="campaign-calling-schedule"
                                className="mt-2 text-sm text-muted-foreground"
                            />
                        </div>

                        <div className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center lg:w-auto lg:justify-end">
                            {/* Live minutes remaining — visible right next to the
                                control that consumes them. Amber when low, red at 0. */}
                            {minutes && !minutes.unlimited && (
                                <span
                                    title={`${minutes.used_minutes} of ${minutes.allocated} plan minutes used this month`}
                                    className={`hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium border ${
                                        outOfMinutes
                                            ? "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20"
                                            : minutes.remaining_minutes <= 30
                                              ? "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400 border-yellow-500/20"
                                              : "bg-muted/40 text-muted-foreground border-border"
                                    }`}
                                >
                                    <Clock className="w-3.5 h-3.5" />
                                    {minutes.remaining_minutes} min left
                                </span>
                            )}
                            <TestAgentButton
                                campaignId={campaignId}
                                disabled={actionLoading}
                                agentName={campaign.script_config?.agent_names?.[0]}
                            />
                            <Button variant="outline" onClick={() => router.push(`/campaigns/${campaignId}/edit`)} disabled={actionLoading}>
                                <Pencil className="w-4 h-4" />
                                Edit
                            </Button>
                            <Button variant="outline" onClick={() => { void duplicate.run(); }} disabled={actionLoading || duplicate.disabled}>
                                <Copy className="w-4 h-4" />
                                Duplicate
                            </Button>
                            {canStart ? (
                                <Button
                                    onClick={handleStartClick}
                                    disabled={actionLoading || outOfMinutes || !readiness.ready}
                                    title={outOfMinutes ? "Out of plan minutes — add minutes to start" : undefined}
                                >
                                    <Play className="w-4 h-4" />
                                    {campaignStartLabel(campaign.status)}
                                </Button>
                            ) : campaign.status === "running" ? (
                                <>
                                    <Button variant="outline" onClick={handlePause} disabled={actionLoading}>
                                        {pausing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Pause className="w-4 h-4" />}
                                        {pausing ? "Pausing…" : "Pause"}
                                    </Button>
                                    <Button variant="outline" onClick={handleStop} disabled={actionLoading}>
                                        {stopping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Square className="w-4 h-4" />}
                                        {stopping ? "Stopping…" : "Stop"}
                                    </Button>
                                </>
                            ) : null}
                        </div>
                    </motion.div>
                    <CampaignDuplicateStatus duplicate={duplicate} />
                    <CampaignControlNoticeBox notice={controlNotice} />
                    {canStart && <CampaignReadinessNotice readiness={readiness} />}

                    {/* Running but not placing calls: say why, in the dialer's own
                        words, and when it will try again. */}
                    {dialBlock && (
                        <div
                            role="status"
                            className={`content-card flex items-start gap-3 ${
                                dialBlock.error ? "border-red-500/30 bg-red-500/5" : "border-amber-500/30 bg-amber-500/5"
                            }`}
                        >
                            <AlertTriangle
                                className={`w-5 h-5 shrink-0 mt-0.5 ${dialBlock.error ? "text-red-500" : "text-amber-500"}`}
                                aria-hidden
                            />
                            <div className="text-sm">
                                <p className={`font-medium ${dialBlock.error ? "text-red-600 dark:text-red-400" : "text-amber-700 dark:text-amber-400"}`}>
                                    {`${dialBlock.testing ? "Testing mode" : "Not dialing"}: ${dialBlock.message}`}
                                </p>
                                {fmtNextAttempt(dialBlock.nextEligibleAt) ? (
                                    <p className="text-muted-foreground mt-0.5">
                                        {`Next attempt: ${fmtNextAttempt(dialBlock.nextEligibleAt)}`}
                                    </p>
                                ) : null}
                            </div>
                        </div>
                    )}

                    {/* Out-of-minutes banner — the campaign can't be started
                        until the tenant has plan minutes again. */}
                    {outOfMinutes && (
                        <div className="content-card border-red-500/30 bg-red-500/5 flex items-start gap-3">
                            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" />
                            <div className="text-sm">
                                <p className="font-medium text-red-600 dark:text-red-400">
                                    Out of plan minutes
                                </p>
                                <p className="text-muted-foreground mt-0.5">
                                    You&apos;ve used {minutes?.used_minutes} of your {minutes?.allocated} monthly
                                    minutes. Campaigns can&apos;t be started until you add minutes or upgrade your plan.
                                </p>
                            </div>
                        </div>
                    )}

                    {/* Why the last Start was refused, in the server's words —
                        beside the quota figures, which refresh underneath it. */}
                    {startError ? (
                        <div role="alert" className="content-card border-red-500/30 bg-red-500/5 flex items-start gap-3">
                            <AlertTriangle className="w-5 h-5 text-red-500 shrink-0 mt-0.5" aria-hidden />
                            <p className="text-sm text-muted-foreground">{startError}</p>
                        </div>
                    ) : null}

                    {/* Active-contacts summary — which lists get dialed on Start.
                        "View more" scrolls to the full lists row below. */}
                    <ActiveContactsSummary
                        lists={lists}
                        onViewMore={() => listsSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
                    />

                    {/* Stats */}
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.1 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-muted/30 rounded-lg">
                                    <Users className="w-5 h-5 text-foreground" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">{stats?.total_leads || 0}</p>
                                    <p className="text-sm text-muted-foreground">Contacts</p>
                                </div>
                            </div>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.12 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 rounded-lg bg-emerald-500/10">
                                    <Users className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-emerald-700 dark:text-emerald-400">{stats?.qualified_leads ?? 0}</p>
                                    <p className="text-sm text-muted-foreground">Qualified Leads</p>
                                </div>
                            </div>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.15 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-muted/30 rounded-lg">
                                    <Phone className="w-5 h-5 text-foreground" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">
                                        {Object.values(stats?.call_outcome_counts || {}).reduce((a, b) => a + b, 0)}
                                    </p>
                                    <p className="text-sm text-muted-foreground">Calls Made</p>
                                </div>
                            </div>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.2 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-emerald-500/20 rounded-lg">
                                    <CheckCircle className="w-5 h-5 text-emerald-400" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">{stats?.goals_achieved || 0}</p>
                                    <p className="text-sm text-muted-foreground">Goals Achieved</p>
                                </div>
                            </div>
                        </motion.div>
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.25 }}
                            className="content-card"
                        >
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-red-500/20 rounded-lg">
                                    <XCircle className="w-5 h-5 text-red-400" />
                                </div>
                                <div>
                                    <p className="text-2xl font-bold text-foreground">
                                        {stats?.call_outcome_counts?.["failed"] || 0}
                                    </p>
                                    <p className="text-sm text-muted-foreground">Failed</p>
                                </div>
                            </div>
                        </motion.div>
                    </div>

                    {/* Live calls — Track B */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                    >
                        <LiveCallsPanel campaignId={campaignId} direction="outbound" />
                    </motion.div>

                    {/* Call issues — why calls aren't going through (only
                        renders when there's something to surface). */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.35 }}
                    >
                        <CallIssuesPanel campaignId={campaignId} direction="outbound" />
                    </motion.div>

                    {/* Contact lists row — toggle active/inactive + call a whole list. */}
                    <div ref={listsSectionRef}>
                        <ContactLists
                            campaignId={campaignId}
                            refreshToken={listsRefreshToken}
                            onListsLoaded={setLists}
                            onCallStarted={() => { void loadData(); }}
                        />
                    </div>

                    {/* Contacts */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.3 }}
                        className="content-card"
                    >
                        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            <h3 className="text-lg font-semibold text-foreground">Contacts</h3>
                            <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:justify-end">
                                <Button size="sm" variant="outline" onClick={() => setCsvModalOpen(true)}>
                                    <Upload className="w-4 h-4" />
                                    Import CSV
                                </Button>
                                <Button size="sm" onClick={() => { setEditingContact(null); setContactForm({ phone_number: "", first_name: "", last_name: "", email: "" }); setShowAddContact(true); }}>
                                    <Plus className="w-4 h-4" />
                                    Add contact
                                </Button>
                            </div>
                        </div>
                        <SmartCsvImport
                            open={csvModalOpen}
                            campaignId={campaignId}
                            onClose={() => setCsvModalOpen(false)}
                            onImported={() => { void loadData(); setListsRefreshToken((t) => t + 1); }}
                        />

                        {showAddContact && (
                            <form onSubmit={handleAddContact} className="mb-6 p-4 bg-muted/30 rounded-lg border border-border">
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                                    <DefaultCountrySelect
                                        id="contact-country"
                                        value={contactCountry}
                                        onChange={setContactCountry}
                                        disabled={addingContact}
                                    />
                                    <div>
                                        <Label htmlFor="phone">Phone Number</Label>
                                        <Input
                                            id="phone"
                                            type="tel"
                                            inputMode="tel"
                                            value={contactForm.phone_number}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, phone_number: e.target.value }))}
                                            placeholder="+1234567890"
                                            required
                                            aria-invalid={contactForm.phone_number.trim() ? !phoneStatus.ok : undefined}
                                            aria-describedby="phone-help phone-status"
                                        />
                                        <p id="phone-help" className="mt-1.5 text-xs text-muted-foreground">{PHONE_HELP_TEXT}</p>
                                        <PhoneStatusLine id="phone-status" raw={contactForm.phone_number} status={phoneStatus} />
                                    </div>
                                    <div>
                                        <Label htmlFor="first_name">First Name</Label>
                                        <Input
                                            id="first_name"
                                            value={contactForm.first_name}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, first_name: e.target.value }))}
                                            placeholder="John"
                                        />
                                    </div>
                                    <div>
                                        <Label htmlFor="last_name">Last Name</Label>
                                        <Input
                                            id="last_name"
                                            value={contactForm.last_name}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, last_name: e.target.value }))}
                                            placeholder="Doe"
                                        />
                                    </div>
                                    <div>
                                        <Label htmlFor="email">Email</Label>
                                        <Input
                                            id="email"
                                            type="email"
                                            value={contactForm.email}
                                            onChange={(e) => setContactForm((prev) => ({ ...prev, email: e.target.value }))}
                                            placeholder="john@example.com"
                                        />
                                    </div>
                                </div>
                                <div className="mb-4 rounded-lg border border-border/70 bg-background/60 p-3" data-testid="contact-more-details">
                                    <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                                        <div>
                                            <div className="text-sm font-semibold text-foreground">More details</div>
                                            <div className="text-xs text-muted-foreground">
                                                Pick any extra field to store with this contact. The agent sees company, title and notes on the call.
                                            </div>
                                        </div>
                                        <div className="w-full sm:w-64">
                                            <Select
                                                value=""
                                                ariaLabel="Add a contact detail"
                                                onChange={(key) => {
                                                    const k = key as OptionalContactFieldKey;
                                                    if (!k || extraContactFields.includes(k)) return;
                                                    setExtraContactFields((prev) => [...prev, k]);
                                                }}
                                            >
                                                <option value="">Add a detail…</option>
                                                {OPTIONAL_CONTACT_FIELDS.filter((f) => !extraContactFields.includes(f.key)).map((f) => (
                                                    <option key={f.key} value={f.key}>{f.label}</option>
                                                ))}
                                            </Select>
                                        </div>
                                    </div>
                                    {extraContactFields.length > 0 ? (
                                        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
                                            {extraContactFields.map((key) => {
                                                const field = OPTIONAL_CONTACT_FIELDS.find((f) => f.key === key)!;
                                                const isNumber = key === "mobile_number" || key === "business_number";
                                                const numberStatus = optionalNumberStatus(key);
                                                return (
                                                    <div key={key}>
                                                        <div className="flex items-center justify-between">
                                                            <Label htmlFor={`contact-${key}`}>{field.label}</Label>
                                                            <button
                                                                type="button"
                                                                className="text-xs text-muted-foreground hover:text-foreground"
                                                                aria-label={`Remove ${field.label}`}
                                                                onClick={() => {
                                                                    setExtraContactFields((prev) => prev.filter((k) => k !== key));
                                                                    setContactForm((prev) => ({ ...prev, [key]: "" }));
                                                                }}
                                                            >
                                                                Remove
                                                            </button>
                                                        </div>
                                                        {field.options ? (
                                                            <Select
                                                                value={String(contactForm[key] ?? "")}
                                                                ariaLabel={field.label}
                                                                onChange={(v) => setContactForm((prev) => ({ ...prev, [key]: v }))}
                                                            >
                                                                <option value="">Choose…</option>
                                                                {field.options.map((o) => (
                                                                    <option key={o} value={o}>{o}</option>
                                                                ))}
                                                            </Select>
                                                        ) : (
                                                            <>
                                                                <Input
                                                                    id={`contact-${key}`}
                                                                    value={String(contactForm[key] ?? "")}
                                                                    onChange={(e) => setContactForm((prev) => ({ ...prev, [key]: e.target.value }))}
                                                                    placeholder={field.placeholder}
                                                                    maxLength={field.maxLength}
                                                                    aria-invalid={numberStatus ? !numberStatus.ok : undefined}
                                                                    aria-describedby={isNumber ? `contact-${key}-status` : undefined}
                                                                />
                                                                {isNumber && (
                                                                    <PhoneStatusLine
                                                                        id={`contact-${key}-status`}
                                                                        raw={String(contactForm[key] ?? "")}
                                                                        status={numberStatus}
                                                                    />
                                                                )}
                                                            </>
                                                        )}
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    ) : null}
                                </div>
                                {contactFormError ? (
                                    <p role="alert" className="mb-3 text-sm text-destructive">{contactFormError}</p>
                                ) : null}
                                <div className="flex gap-2">
                                    <Button type="submit" size="sm" disabled={addingContact || !contactFormValid}>
                                        {addingContact ? <Loader2 className="w-4 h-4 animate-spin" /> : (editingContact ? "Save changes" : "Add")}
                                    </Button>
                                    <Button type="button" variant="outline" size="sm" onClick={() => { setShowAddContact(false); setEditingContact(null); setExtraContactFields([]); setContactFormError(""); }}>
                                        Cancel
                                    </Button>
                                </div>
                            </form>
                        )}

                        {contacts.length === 0 ? (
                            <EmptyState title="No contacts yet. Add contacts to start your campaign." />
                        ) : (
                          <>
                            {/* Search + lead filter toolbar */}
                            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div className="relative w-full sm:max-w-xs">
                                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                    <input
                                        type="text"
                                        value={contactSearch}
                                        onChange={(e) => setContactSearch(e.target.value)}
                                        placeholder="Search by phone, name or email…"
                                        className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
                                    />
                                </div>
                                <div className="flex items-center gap-1 rounded-lg border border-border bg-background p-1">
                                    {([
                                        ["all", "All"],
                                        ["leads", "Leads"],
                                    ] as const).map(([key, label]) => (
                                        <button
                                            key={key}
                                            type="button"
                                            onClick={() => setContactFilter(key)}
                                            className={`rounded-md px-3 py-1 text-xs font-semibold transition-colors duration-150 ease-out ${
                                                contactFilter === key
                                                    ? "bg-accent text-accent-foreground"
                                                    : "text-muted-foreground hover:text-foreground"
                                            }`}
                                        >
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {filteredContacts.length === 0 ? (
                              <div className="py-8 text-center text-muted-foreground">
                                No contacts match your search or filter.
                              </div>
                            ) : (
                              <div className="overflow-x-auto">
                                <table className="w-full">
                                    <thead className="border-b border-border">
                                        <tr>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Phone</th>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Name</th>
                                            <th className="px-4 py-2 text-left text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Status</th>
                                            <th className="px-4 py-2 text-right text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Attempts</th>
                                            <th className="px-4 py-2 text-right text-xs font-medium text-muted-foreground uppercase whitespace-nowrap">Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-border/60">
                                        {filteredContacts.map((contact) => (
                                            <tr key={contact.id} className={`transition-colors hover:bg-muted/30 ${contact.is_lead ? "bg-green-500/5" : ""}`}>
                                                <td className="px-4 py-3 text-sm text-foreground tabular-nums whitespace-nowrap">{contact.phone_number}</td>
                                                <td className="px-4 py-3 text-sm text-muted-foreground whitespace-nowrap">
                                                    {contact.first_name || contact.last_name
                                                        ? `${contact.first_name || ""} ${contact.last_name || ""}`.trim()
                                                        : "Name unavailable"}
                                                    <ContactCapturedDetails leadId={contact.id} />
                                                </td>
                                                <td className="px-4 py-3 text-sm whitespace-nowrap">
                                                    {contact.is_lead ? (
                                                        <div className="flex flex-col gap-0.5">
                                                            <span
                                                                className="w-fit rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400"
                                                                title={contact.follow_up_note ?? undefined}
                                                            >
                                                                Lead — follow up
                                                            </span>
                                                            {contact.follow_up_note ? (
                                                                <span className="max-w-[16rem] truncate text-xs text-muted-foreground" title={contact.follow_up_note}>
                                                                    {contact.follow_up_note}
                                                                </span>
                                                            ) : null}
                                                        </div>
                                                    ) : (
                                                        <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${getContactStatusStyle(contact.last_call_result)}`}>
                                                            {humanizeOutcome(contact.last_call_result)}
                                                        </span>
                                                    )}
                                                    {contact.latest_analysis_note && (
                                                        <p className="mt-1 max-w-xs whitespace-normal text-xs text-muted-foreground">
                                                            Latest call analysis: {contact.latest_analysis_note}
                                                        </p>
                                                    )}
                                                </td>
                                                <td className="px-4 py-3 text-right text-sm text-muted-foreground tabular-nums whitespace-nowrap">{contact.call_attempts}</td>
                                                <td className="px-4 py-3 text-right whitespace-nowrap">
                                                    <div className="flex items-center justify-end gap-1">
                                                        <button
                                                            type="button"
                                                            onClick={() => startEditContact(contact)}
                                                            aria-label={`Edit ${contact.phone_number}`}
                                                            title="Edit contact"
                                                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                                        >
                                                            <Pencil className="h-3.5 w-3.5" />
                                                        </button>
                                                        <button
                                                            type="button"
                                                            onClick={() => setRemoveTarget({ id: contact.id, phone: contact.phone_number })}
                                                            disabled={deletingContactId === contact.id}
                                                            aria-label={`Remove ${contact.phone_number}`}
                                                            title="Remove contact"
                                                            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                                                        >
                                                            {deletingContactId === contact.id ? (
                                                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                            ) : (
                                                                <Trash2 className="h-3.5 w-3.5" />
                                                            )}
                                                        </button>
                                                    </div>
                                                    {contactDeleteError?.id === contact.id ? (
                                                        <p role="alert" className="mt-1 whitespace-normal text-xs text-destructive">{contactDeleteError.message}</p>
                                                    ) : null}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                              </div>
                            )}
                          </>
                        )}
                    </motion.div>

                    {/* Knowledge base — vectorless-RAG tree the agent answers from */}
                    <KnowledgePanel campaignId={campaignId} readOnly={!canManageKnowledge} />

                    {/* Script Card — transcripts of every call with timestamps */}
                    <ScriptCard campaignId={campaignId} />
                </div>
            ) : null}

            <CampaignStartDialog dialog={startDialog} />
            <ConfirmDialog
                open={removeTarget !== null}
                onOpenChange={(open) => { if (!open) setRemoveTarget(null); }}
                intent="delete"
                title="Remove contact"
                description=""
                warningText={removeTarget ? `Remove ${removeTarget.phone} from this campaign? It will no longer be dialed.` : ""}
                confirmLabel="Remove contact"
                onConfirm={() => (removeTarget ? removeContact(removeTarget.id) : undefined)}
            />
        </DashboardLayout>
    );
}
