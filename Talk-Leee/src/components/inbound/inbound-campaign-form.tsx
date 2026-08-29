"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { AlertCircle, ArrowLeft, Bot, CalendarClock, Loader2, LockKeyhole, Mic2, PhoneForwarded, Plus, Save, ShieldCheck, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCampaigns } from "@/lib/api-hooks";
import { inboundErrorCode, inboundErrorKind, type InboundCampaign, type InboundCampaignInput, type InboundPhoneNumber } from "@/lib/inbound-api";
import {
    INBOUND_AFTER_HOURS_OPTIONS,
    initialInboundCampaignInput,
    isEligibleInboundBaseCampaign,
    isEligibleInboundTrunk,
    validateInboundCampaign,
    verifiedTransferConfigurationAvailable,
    type InboundFormErrors,
} from "@/lib/inbound-validation";
import { useInboundPhoneNumbers, useInboundRuntimeCapabilities } from "@/lib/queries/inbound-queries";
import { useSipTrunks } from "@/lib/telephony-api";
import { cn } from "@/lib/utils";

const TIMEZONES = [
    "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "Europe/London", "Europe/Berlin", "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata",
    "Asia/Singapore", "Australia/Sydney",
];
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export function InboundCampaignForm({ mode, initialValue, pending, canAssignNumber, onSubmit }: {
    mode: "create" | "edit";
    initialValue?: InboundCampaign;
    pending: boolean;
    canAssignNumber: boolean;
    onSubmit: (value: InboundCampaignInput) => Promise<void>;
}) {
    const campaignsQuery = useCampaigns();
    const trunksQuery = useSipTrunks();
    const numbersQuery = useInboundPhoneNumbers(canAssignNumber);
    const runtimeCapabilitiesQuery = useInboundRuntimeCapabilities(initialValue?.id);
    const [value, setValue] = useState<InboundCampaignInput>(() => initialInboundCampaignInput(initialValue));
    const [errors, setErrors] = useState<InboundFormErrors>({});
    const errorSummaryRef = useRef<HTMLDivElement | null>(null);

    const campaigns = useMemo(() => (campaignsQuery.data ?? []).filter((campaign) => campaign.status !== "deleted"), [campaignsQuery.data]);
    const eligibleCampaigns = useMemo(() => campaigns.filter(isEligibleInboundBaseCampaign), [campaigns]);
    const inboundTrunks = useMemo(() => (trunksQuery.data ?? []).filter((trunk) => trunk.direction === "inbound" || trunk.direction === "both"), [trunksQuery.data]);
    const eligibleInboundTrunks = useMemo(() => inboundTrunks.filter(isEligibleInboundTrunk), [inboundTrunks]);
    const availableNumbers = useMemo(() => {
        const rows = numbersQuery.data ?? [];
        const current = initialValue?.phone_number;
        return current && !rows.some((number) => number.id === current.id) ? [current, ...rows] : rows;
    }, [initialValue?.phone_number, numbersQuery.data]);
    const timezoneOptions = useMemo(() => Array.from(new Set([...TIMEZONES, value.timezone || "UTC"])), [value.timezone]);
    const dependenciesLoading = campaignsQuery.isLoading || trunksQuery.isLoading || (canAssignNumber && numbersQuery.isLoading);
    const dependencyError = campaignsQuery.isError || trunksQuery.isError || (canAssignNumber && numbersQuery.isError);
    const transferConfigurationAvailable = verifiedTransferConfigurationAvailable(runtimeCapabilitiesQuery);

    function update<K extends keyof InboundCampaignInput>(key: K, next: InboundCampaignInput[K]) {
        setValue((current) => ({ ...current, [key]: next }));
        setErrors((current) => ({ ...current, [key]: undefined, form: undefined }));
    }

    function updateSchedule(day: number, patch: Partial<InboundCampaignInput["weekly_schedule"][number]>) {
        setValue((current) => ({
            ...current,
            weekly_schedule: current.weekly_schedule.map((entry) => entry.day === day ? { ...entry, ...patch } : entry),
        }));
        setErrors((current) => ({ ...current, weekly_schedule: undefined, form: undefined }));
    }

    function updateTimeWindow(day: number, index: number, key: "start" | "end", next: string) {
        const schedule = value.weekly_schedule.find((entry) => entry.day === day);
        if (!schedule) return;
        const windows = [...(schedule.windows?.length ? schedule.windows : [{ start: schedule.start, end: schedule.end }])];
        windows[index] = { ...windows[index], [key]: next };
        updateSchedule(day, {
            windows,
            ...(index === 0 ? { [key]: next } : {}),
        });
    }

    function showErrors(next: InboundFormErrors) {
        setErrors(next);
        window.requestAnimationFrame(() => errorSummaryRef.current?.focus());
    }

    async function save(event: React.FormEvent) {
        event.preventDefault();
        if (pending) return;
        const transferRequested = value.after_hours_action === "transfer" || value.transfer_enabled;
        const refreshedCapabilities = transferRequested
            ? await runtimeCapabilitiesQuery.refetch()
            : runtimeCapabilitiesQuery;
        const verifiedTransferAvailable = verifiedTransferConfigurationAvailable(refreshedCapabilities);
        const nextErrors = validateInboundCampaign(value, {
            transferConfigurationAvailable: verifiedTransferAvailable,
        });
        if (
            value.campaign_id
            && !(mode === "edit" && value.campaign_id === initialValue?.campaign_id)
            && !eligibleCampaigns.some((campaign) => campaign.id === value.campaign_id)
        ) {
            nextErrors.campaign_id = "Choose an inbound-ready AI campaign that is not completed or cancelled.";
        }
        if (value.sip_trunk_id && !eligibleInboundTrunks.some((trunk) => trunk.id === value.sip_trunk_id)) {
            nextErrors.sip_trunk_id = "Choose an inbound trunk with fresh Asterisk runtime proof.";
        }
        if (Object.keys(nextErrors).length > 0) return showErrors(nextErrors);
        try {
            await onSubmit(value);
        } catch (error) {
            const kind = inboundErrorKind(error);
            const code = inboundErrorCode(error);
            const transferGateClosed = code === "transfer_runtime_unavailable"
                || code === "transfer_platform_disabled"
                || code === "transfer_staging_scope_mismatch";
            showErrors({
                form: transferGateClosed && error instanceof Error
                    ? error.message
                    : kind === "conflict"
                    ? "This campaign changed in another session. Reload the latest version, review it, and apply your changes again."
                    : kind === "forbidden"
                        ? "Your current role does not allow this change. Ask a tenant administrator to review your access."
                        : error instanceof Error ? error.message : "The configuration could not be saved.",
            });
        }
    }

    return (
        <form onSubmit={save} className="space-y-6" noValidate>
            <div className="flex flex-wrap items-center justify-between gap-3">
                <Button asChild variant="ghost" size="sm" className="px-2">
                    <Link href={mode === "edit" && initialValue ? `/inbound-campaigns/${initialValue.id}` : "/inbound-campaigns"}>
                        <ArrowLeft className="h-4 w-4" aria-hidden />Back to inbound campaigns
                    </Link>
                </Button>
                <div className="inline-flex items-center gap-2 rounded-full border border-border bg-muted/40 px-3 py-1.5 text-xs font-medium text-muted-foreground">
                    <LockKeyhole className="h-3.5 w-3.5" aria-hidden />Save is separate from activation
                </div>
            </div>

            {Object.values(errors).some(Boolean) ? (
                <div ref={errorSummaryRef} tabIndex={-1} className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 outline-none focus-visible:ring-2 focus-visible:ring-ring" role="alert" aria-labelledby="inbound-error-title">
                    <div className="flex items-start gap-3">
                        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden />
                        <div>
                            <p id="inbound-error-title" className="text-sm font-semibold text-destructive">Review this configuration</p>
                            <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-muted-foreground">
                                {Object.entries(errors).filter(([, message]) => Boolean(message)).map(([key, message]) => <li key={key}>{message}</li>)}
                            </ul>
                            {errors.form?.includes("changed in another session") ? <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => window.location.reload()}>Reload latest version</Button> : null}
                        </div>
                    </div>
                </div>
            ) : null}

            <section className="content-card space-y-6" aria-labelledby="inbound-routing-heading">
                <div><h2 id="inbound-routing-heading" className="text-lg font-semibold text-foreground">Number and routing</h2><p className="mt-1 text-sm text-muted-foreground">Bind one verified public number to one AI campaign and one inbound-capable trunk.</p></div>
                <div className="grid gap-5 md:grid-cols-2">
                    <Field label="Campaign name" htmlFor="inbound-name" error={errors.name}>
                        <Input id="inbound-name" value={value.name} onChange={(event) => update("name", event.target.value)} placeholder="Main support line" autoComplete="off" aria-invalid={Boolean(errors.name)} />
                    </Field>
                    <Field label="AI campaign" htmlFor="inbound-campaign_id" error={errors.campaign_id} hint={mode === "edit" ? "The base AI campaign is locked after creation so edits cannot bypass the audited inbound lifecycle." : "Only inbound-ready campaigns and unused outbound drafts are selectable; the server performs the final activity check."}>
                        <select id="inbound-campaign_id" value={value.campaign_id} onChange={(event) => update("campaign_id", event.target.value)} disabled={mode === "edit" || dependenciesLoading || eligibleCampaigns.length === 0} aria-invalid={Boolean(errors.campaign_id)} className={selectClass}>
                            <option value="">Choose an AI campaign</option>{campaigns.map((campaign) => {
                                const eligible = isEligibleInboundBaseCampaign(campaign);
                                return <option key={campaign.id} value={campaign.id} disabled={!eligible}>{campaign.name} · {campaign.status}{eligible ? "" : " · unavailable"}</option>;
                            })}
                        </select>
                    </Field>
                </div>

                <fieldset className="space-y-3" aria-describedby={errors.did_number ? "inbound-did-error" : "inbound-did-help"}>
                    <legend className="text-sm font-medium text-foreground">Verified public phone number</legend>
                    <p id="inbound-did-help" className="text-xs text-muted-foreground">Numbers are masked and loaded from verified tenant inventory. The server rejects stale or duplicate assignments.</p>
                    {dependenciesLoading ? <InlineState kind="loading">Loading verified numbers and trunks…</InlineState> : dependencyError ? <InlineState kind="error">Verified inventory or SIP trunks could not be loaded. Retry this page.</InlineState> : availableNumbers.length === 0 ? <InlineState kind="error">No verified number is available. Ask an administrator to verify a DID first.</InlineState> : (
                        <div className="grid gap-2 sm:grid-cols-2">
                            {availableNumbers.map((number) => <NumberOption key={number.id} number={number} checked={value.did_number === number.e164} disabled={!canAssignNumber && value.did_number !== number.e164} current={number.id === initialValue?.phone_number?.id} onSelect={() => update("did_number", number.e164 ?? "")} />)}
                        </div>
                    )}
                    {errors.did_number ? <p id="inbound-did-error" className="text-xs font-medium text-destructive">{errors.did_number}</p> : null}
                </fieldset>

                <div className="grid gap-5 md:grid-cols-2">
                    <Field label="Inbound SIP trunk" htmlFor="inbound-sip_trunk_id" error={errors.sip_trunk_id} hint="Only trunks with fresh Asterisk endpoint or registration proof can be selected; the server rechecks this before every admitted call.">
                        <select id="inbound-sip_trunk_id" value={value.sip_trunk_id} onChange={(event) => update("sip_trunk_id", event.target.value)} disabled={dependenciesLoading || eligibleInboundTrunks.length === 0 || (mode === "edit" && !canAssignNumber)} aria-invalid={Boolean(errors.sip_trunk_id)} className={selectClass}>
                            <option value="">Choose an inbound trunk</option>{inboundTrunks.map((trunk) => <option key={trunk.id} value={trunk.id} disabled={!isEligibleInboundTrunk(trunk)}>{trunk.trunk_name} · {trunk.runtime_ready ? "runtime ready" : trunk.runtime_status_detail}</option>)}
                        </select>
                    </Field>
                    <Field label="Business timezone" htmlFor="inbound-timezone" error={errors.timezone}>
                        <select id="inbound-timezone" value={value.timezone} onChange={(event) => update("timezone", event.target.value)} aria-invalid={Boolean(errors.timezone)} className={selectClass}>{timezoneOptions.map((timezone) => <option key={timezone} value={timezone}>{timezone}</option>)}</select>
                    </Field>
                </div>
            </section>

            <section className="content-card space-y-6" aria-labelledby="inbound-agent-heading">
                <div className="flex items-start gap-3"><Bot className="mt-0.5 h-5 w-5 text-primary" aria-hidden /><div><h2 id="inbound-agent-heading" className="text-lg font-semibold text-foreground">AI behavior</h2><p className="mt-1 text-sm text-muted-foreground">Blank fields inherit the selected campaign. Inbound purpose, style, instructions, voice, and opening-silence settings are pinned before Answer.</p></div></div>
                <div className="rounded-xl border border-sky-500/30 bg-sky-500/5 p-3 text-sm text-foreground" role="status"><strong>Knowledge and executable tools stay campaign-owned.</strong> Configure those on the selected base AI campaign; this route cannot silently invent capabilities.</div>
                <div className="grid gap-5 md:grid-cols-2">
                    <Field label="Inbound call purpose" htmlFor="inbound-purpose" hint="Added to the base campaign goal for this number only."><Input id="inbound-purpose" value={value.purpose ?? ""} onChange={(event) => update("purpose", event.target.value)} maxLength={2000} placeholder="Handle new enquiries and arrange the right next step" /></Field>
                    <Field label="Inbound agent style" htmlFor="inbound-agent_persona" hint="A short behavior description; the approved base persona remains intact."><Input id="inbound-agent_persona" value={value.agent_persona ?? ""} onChange={(event) => update("agent_persona", event.target.value)} maxLength={2000} placeholder="Warm, concise receptionist" /></Field>
                </div>
                <Field label="System prompt override" htmlFor="inbound-system_prompt" hint="Appended to—not substituted for—the approved base campaign instructions."><textarea id="inbound-system_prompt" value={value.system_prompt ?? ""} onChange={(event) => update("system_prompt", event.target.value)} rows={7} maxLength={20000} placeholder="Optional inbound-specific instructions…" className={textareaClass} /></Field>
                <div className="grid gap-5 md:grid-cols-2">
                    <Field label="Voice ID override" htmlFor="inbound-voice_id" hint="Leave blank to inherit the base campaign voice."><Input id="inbound-voice_id" value={value.voice_id ?? ""} onChange={(event) => update("voice_id", event.target.value)} maxLength={255} placeholder="Inherit from campaign" /></Field>
                </div>
            </section>

            <section className="content-card space-y-6" aria-labelledby="inbound-opening-heading">
                <div className="flex items-start gap-3"><Mic2 className="mt-0.5 h-5 w-5 text-primary" aria-hidden /><div><h2 id="inbound-opening-heading" className="text-lg font-semibold text-foreground">Opening and turn-taking</h2><p className="mt-1 text-sm text-muted-foreground">Choose who speaks first and how long a caller-first line waits before checking in.</p></div></div>
                <div className="grid gap-5 md:grid-cols-2">
                    <Field label="Opening mode" htmlFor="inbound-opening_mode">
                        <select id="inbound-opening_mode" value={value.opening_mode} onChange={(event) => update("opening_mode", event.target.value as InboundCampaignInput["opening_mode"])} className={selectClass}><option value="caller_first">Caller first</option><option value="agent_first">Agent greeting first</option></select>
                    </Field>
                    <Field label="Opening silence timeout" htmlFor="inbound-silence_timeout_seconds" error={errors.silence_timeout_seconds} hint="Between 3 and 60 seconds."><Input id="inbound-silence_timeout_seconds" type="number" min={3} max={60} value={value.silence_timeout_seconds} onChange={(event) => update("silence_timeout_seconds", Number(event.target.value))} aria-invalid={Boolean(errors.silence_timeout_seconds)} /></Field>
                </div>
                <Field label="Opening greeting" htmlFor="inbound-greeting" error={errors.greeting} hint={value.opening_mode === "agent_first" ? "Required and played only after the call is admitted." : "Optional acknowledgement used after the caller's first turn."}><textarea id="inbound-greeting" value={value.greeting} onChange={(event) => update("greeting", event.target.value)} rows={3} placeholder="Hello, thanks for calling. How can I help?" aria-invalid={Boolean(errors.greeting)} className={textareaClass} /></Field>
            </section>

            <section className="content-card space-y-6" aria-labelledby="inbound-hours-heading">
                <div className="flex items-start gap-3"><CalendarClock className="mt-0.5 h-5 w-5 text-primary" aria-hidden /><div><h2 id="inbound-hours-heading" className="text-lg font-semibold text-foreground">Business hours and after-hours</h2><p className="mt-1 text-sm text-muted-foreground">Times use {value.timezone}. A window ending before it starts crosses midnight; add a second window for a split shift.</p></div></div>
                <div className="space-y-3">
                    {value.weekly_schedule.map((entry) => {
                        const windows = entry.windows?.length ? entry.windows : [{ start: entry.start, end: entry.end }];
                        return (
                            <div key={entry.day} className="rounded-xl border border-border bg-muted/20 p-3">
                                <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
                                    <label className="flex w-36 shrink-0 items-center gap-2 text-sm font-medium text-foreground"><input type="checkbox" checked={entry.enabled} onChange={(event) => updateSchedule(entry.day, { enabled: event.target.checked })} className="h-4 w-4 rounded border-input accent-primary" />{DAY_NAMES[entry.day] ?? `Day ${entry.day + 1}`}</label>
                                    <div className="flex-1 space-y-2">
                                        {entry.enabled ? windows.map((window, index) => (
                                            <div key={`${entry.day}-${index}`} className="flex flex-wrap items-center gap-2">
                                                <Input type="time" value={window.start} onChange={(event) => updateTimeWindow(entry.day, index, "start", event.target.value)} aria-label={`${DAY_NAMES[entry.day]} window ${index + 1} start`} className="w-32" />
                                                <span className="text-xs text-muted-foreground">to</span>
                                                <Input type="time" value={window.end} onChange={(event) => updateTimeWindow(entry.day, index, "end", event.target.value)} aria-label={`${DAY_NAMES[entry.day]} window ${index + 1} end`} className="w-32" />
                                                {index > 0 ? <Button type="button" variant="ghost" size="sm" onClick={() => updateSchedule(entry.day, { windows: windows.filter((_, windowIndex) => windowIndex !== index) })}><Trash2 className="h-4 w-4" aria-hidden />Remove</Button> : null}
                                            </div>
                                        )) : <p className="text-sm text-muted-foreground">Closed</p>}
                                        {entry.enabled && windows.length < 2 ? <Button type="button" variant="ghost" size="sm" onClick={() => updateSchedule(entry.day, { windows: [...windows, { start: "13:00", end: "17:00" }] })}><Plus className="h-4 w-4" aria-hidden />Add split window</Button> : null}
                                    </div>
                                </div>
                            </div>
                        );
                    })}
                </div>
                <div className="grid gap-5 md:grid-cols-2">
                    <Field label="Holiday policy" htmlFor="inbound-holiday_policy"><select id="inbound-holiday_policy" value={value.holiday_policy} onChange={(event) => update("holiday_policy", event.target.value as InboundCampaignInput["holiday_policy"])} className={selectClass}><option value="closed">Use after-hours action</option><option value="regular_hours">Follow regular weekly hours</option></select></Field>
                    <Field label="After-hours action" htmlFor="inbound-after_hours_action" error={errors.after_hours_action}><select id="inbound-after_hours_action" value={value.after_hours_action} onChange={(event) => update("after_hours_action", event.target.value as InboundCampaignInput["after_hours_action"])} className={selectClass} aria-invalid={Boolean(errors.after_hours_action)}>{INBOUND_AFTER_HOURS_OPTIONS.map((option) => {
                        const available = option.runtimeSupported || (option.value === "transfer" && transferConfigurationAvailable);
                        return <option key={option.value} value={option.value} disabled={!available}>{option.value === "transfer" && available ? "Transfer (controlled proof window)" : option.label}</option>;
                    })}</select></Field>
                </div>
                <div className="rounded-xl border border-sky-500/30 bg-sky-500/5 p-3 text-sm text-foreground" role="status"><strong>{transferConfigurationAvailable ? "The controlled transfer proof window is open." : "Two after-hours routes are currently available."}</strong> {transferConfigurationAvailable ? "Transfer is still protected by the campaign allowlist, bidirectional trunk, attempt/hop limits, hard deadline, and server-side admission." : "Reject ends before Answer, while AI message intake stores the response in regular call history. Transfer remains blocked until both server capability gates pass."}</div>
                {value.after_hours_action === "voicemail" ? <Field label="AI intake opening message" htmlFor="inbound-after_hours_message" hint="The AI says this before collecting the caller's response in the normal call transcript; no dedicated voicemail notification is created."><textarea id="inbound-after_hours_message" value={value.after_hours_message ?? ""} onChange={(event) => update("after_hours_message", event.target.value)} rows={3} placeholder="We are currently unavailable. Please tell our AI your name, number, and a short message." className={textareaClass} /></Field> : null}
                {value.after_hours_action === "transfer" ? <Field label="After-hours transfer destination" htmlFor="inbound-transfer_number" error={errors.transfer_number}><Input id="inbound-transfer_number" value={value.transfer_number ?? ""} onChange={(event) => update("transfer_number", event.target.value)} placeholder="+14155550199" inputMode="tel" aria-invalid={Boolean(errors.transfer_number)} /></Field> : null}
            </section>

            <section className="content-card space-y-6" aria-labelledby="inbound-transfer-heading">
                <div className="flex items-start gap-3"><PhoneForwarded className="mt-0.5 h-5 w-5 text-primary" aria-hidden /><div><h2 id="inbound-transfer-heading" className="text-lg font-semibold text-foreground">Saved transfer policy</h2><p className="mt-1 text-sm text-muted-foreground">{transferConfigurationAvailable ? "Configure only the approved destinations and limits for this controlled proof window." : "Legacy policy fields remain visible for audit and disablement. New inbound transfer configuration is unavailable."}</p></div></div>
                <label className={cn("flex items-start gap-3 rounded-xl border border-border bg-muted/30 p-4", transferConfigurationAvailable || value.transfer_enabled ? "cursor-pointer" : "cursor-not-allowed opacity-75")}><input type="checkbox" checked={value.transfer_enabled} disabled={!transferConfigurationAvailable && !value.transfer_enabled} onChange={(event) => update("transfer_enabled", event.target.checked)} className="mt-1 h-4 w-4 rounded border-input accent-primary" /><span><span className="block text-sm font-semibold text-foreground">{transferConfigurationAvailable ? "Controlled inbound transfer" : "Controlled inbound transfer unavailable"}</span><span className="mt-0.5 block text-sm text-muted-foreground">{transferConfigurationAvailable ? "Enable only for a signed staging test; the backend rechecks every gate before creating the second leg." : "Existing enabled policies can be switched off. New enablement stays locked until linked-leg ownership, hard-cap teardown, carrier behavior, and settlement are verified."}</span></span></label>
                {runtimeCapabilitiesQuery.isError ? <p className="text-sm text-destructive" role="alert">Transfer capability could not be verified, so new transfer configuration remains locked.</p> : null}
                {errors.transfer_enabled ? <p className="text-sm text-destructive" role="alert">{errors.transfer_enabled}</p> : null}
                {value.transfer_enabled ? (
                    <>
                        <Field label="Approved transfer destinations" htmlFor="inbound-transfer_destinations" error={errors.transfer_destinations} hint="One E.164 number per line or comma-separated."><textarea id="inbound-transfer_destinations" value={(value.transfer_destinations ?? []).join("\n")} onChange={(event) => update("transfer_destinations", event.target.value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean))} rows={4} placeholder={"+14155550199\n+14155550200"} aria-invalid={Boolean(errors.transfer_destinations)} className={textareaClass} /></Field>
                        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-4">
                            <Field label="Failure action" htmlFor="inbound-transfer_failure_action"><select id="inbound-transfer_failure_action" value={value.transfer_failure_action} onChange={(event) => update("transfer_failure_action", event.target.value as InboundCampaignInput["transfer_failure_action"])} className={selectClass}><option value="voicemail">AI message intake</option><option value="return_to_agent">Return to agent</option><option value="hangup">Hang up</option></select></Field>
                            <Field label="Maximum attempts" htmlFor="inbound-max_transfer_attempts"><Input id="inbound-max_transfer_attempts" type="number" min={1} max={5} value={value.max_transfer_attempts} onChange={(event) => update("max_transfer_attempts", Number(event.target.value))} /></Field>
                            <Field label="Maximum hops" htmlFor="inbound-max_transfer_hops"><Input id="inbound-max_transfer_hops" type="number" min={1} max={5} value={value.max_transfer_hops} onChange={(event) => update("max_transfer_hops", Number(event.target.value))} /></Field>
                            <Field label="Max call seconds" htmlFor="inbound-max_call_duration"><Input id="inbound-max_call_duration" type="number" min={60} max={14400} step={60} value={value.max_call_duration_seconds} onChange={(event) => update("max_call_duration_seconds", Number(event.target.value))} /></Field>
                        </div>
                    </>
                ) : null}
            </section>

            <section className="content-card space-y-6" aria-labelledby="inbound-recording-heading">
                <div><h2 id="inbound-recording-heading" className="text-lg font-semibold text-foreground">Recording safety</h2><p className="mt-1 text-sm text-muted-foreground">Recording remains fail-closed unless campaign, tenant, platform, and disclosure gates all permit it.</p></div>
                <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-muted/30 p-4"><input type="checkbox" checked={value.recording_enabled} onChange={(event) => update("recording_enabled", event.target.checked)} className="mt-1 h-4 w-4 rounded border-input accent-primary" /><span><span className="block text-sm font-semibold text-foreground">Record inbound calls</span><span className="mt-0.5 block text-sm text-muted-foreground">Enable only after approving consent, retention, and access rules for every served jurisdiction.</span></span></label>
                {value.recording_enabled ? <Field label="Recording disclosure" htmlFor="inbound-consent_message" error={errors.consent_message} hint="This text must be played before recording begins."><textarea id="inbound-consent_message" value={value.consent_message ?? ""} onChange={(event) => update("consent_message", event.target.value)} rows={3} placeholder="This call may be recorded for quality and training purposes." aria-invalid={Boolean(errors.consent_message)} className={textareaClass} /></Field> : null}
            </section>

            <div className="flex flex-col-reverse gap-3 rounded-xl border border-border bg-muted/20 p-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-2 text-sm text-muted-foreground"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /><span>Saving never activates the number. The server evaluates readiness again during confirmed activation.</span></div>
                <Button type="submit" disabled={pending || dependenciesLoading || dependencyError || eligibleCampaigns.length === 0 || eligibleInboundTrunks.length === 0 || availableNumbers.length === 0}>
                    {pending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Save className="h-4 w-4" aria-hidden />}{pending ? "Saving…" : mode === "create" ? "Create inactive campaign" : "Save changes"}
                </Button>
            </div>
        </form>
    );
}

function NumberOption({ number, checked, disabled, current, onSelect }: { number: InboundPhoneNumber; checked: boolean; disabled: boolean; current: boolean; onSelect: () => void }) {
    return <label className={cn("flex items-start gap-3 rounded-xl border p-3", checked ? "border-primary bg-primary/5" : "border-border bg-background", disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer")}><input type="radio" name="verified-phone-number" value={number.id} checked={checked} disabled={disabled} onChange={onSelect} className="mt-1 h-4 w-4 accent-primary" /><span className="min-w-0"><span className="block text-sm font-medium text-foreground">{number.label || "Verified number"}</span><span className="block font-mono text-sm text-muted-foreground">{number.masked_number}</span><span className="mt-1 block text-xs text-emerald-700 dark:text-emerald-300">Verified{current ? " · Current assignment" : " · Available"}</span></span></label>;
}

function Field({ label, htmlFor, hint, error, children }: { label: string; htmlFor: string; hint?: string; error?: string; children: React.ReactNode }) {
    return <div className="space-y-2"><Label htmlFor={htmlFor}>{label}</Label>{children}{error ? <p id={`${htmlFor}-error`} className="text-xs font-medium text-destructive">{error}</p> : hint ? <p id={`${htmlFor}-hint`} className="text-xs text-muted-foreground">{hint}</p> : null}</div>;
}

function InlineState({ kind, children }: { kind: "loading" | "error"; children: React.ReactNode }) {
    return <div className={cn("flex items-start gap-2 rounded-xl border p-3 text-sm", kind === "error" ? "border-amber-500/30 bg-amber-500/5 text-foreground" : "border-border bg-muted/30 text-muted-foreground")} role={kind === "loading" ? "status" : "alert"}>{kind === "loading" ? <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" aria-hidden /> : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />}{children}</div>;
}

const selectClass = "h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";
const textareaClass = "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
