"use client";

/**
 * Campaign creation wizard — the simplified, knowledge-first flow.
 *
 * Three steps:
 *   1. Basics    — name, company, persona, agent names, voice+provider, goal.
 *   2. Knowledge — upload one .md/.txt; this IS the campaign's content.
 *   3. Review    — preview the composed prompt + greeting, then create.
 *
 * Creates with knowledge_driven:true (lean prompt, no slot slog) AND a
 * per-campaign tts_provider+voice (no account-wide side effect), then uploads
 * the knowledge doc. The classic slot form still lives at <CampaignForm>.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, BookOpen, Check, FileText, Loader2, Upload, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, PersonaType, CampaignCallingSchedule } from "@/lib/dashboard-api";
import { api } from "@/lib/api";
import { AgentNameGender, pruneGenders } from "@/components/campaigns/agent-name-gender";
import { MAX_BRIEF_NAME_LENGTH, PERSONAS, parseAgentNames, representativeNameTooLong } from "@/lib/campaign-personas";
import { VoiceProviderPicker } from "@/components/campaigns/voice-provider-picker";
import { CallingScheduleEditor } from "@/components/campaigns/calling-schedule-editor";
import { callingSchedulePayload, defaultCallingSchedule } from "@/lib/calling-schedule-text";
import { CampaignBriefFields } from "@/components/campaigns/campaign-brief-fields";
import { PromptLayerPreview } from "@/components/campaigns/prompt-layer-preview";
import {
    buildCampaignBrief,
    campaignBriefGuidanceText,
    campaignBriefValidation,
    EMPTY_CAMPAIGN_BRIEF_DRAFT,
    type CampaignBriefDraft,
} from "@/lib/campaign-brief";
import { guidanceBudgetStatus } from "@/lib/campaign-guidance";
import {
    CampaignLeadFieldsPicker,
    useCampaignLeadFieldDraft,
} from "@/components/campaigns/campaign-lead-fields";
import { leadDetailsApi, type CampaignLeadField } from "@/lib/lead-details-api";
import { callingHoursInverted } from "@/lib/calling-window";
import { DraftRestoredNotice } from "@/components/campaigns/draft-restored-notice";

/**
 * Browser-only draft retention for `draftStorageKey` callers:
 * `/inbound-campaigns/new` step 1 (see that page for the key scheme) and,
 * since QA F1-22, outbound `/campaigns/new` (one key per account). Every
 * read/write below is a no-op without a key. A knowledge `File` cannot be
 * serialized, so only whether one was attached is remembered; `hadFile`
 * drives a restore notice asking the user to re-attach it.
 */
import { CampaignVoiceSettings, type CampaignVoiceSettingsValue } from "@/components/realtime/campaign-settings";

type CampaignWizardDraft = {
    voiceSelection?: CampaignVoiceSettingsValue;
    step: number;
    name: string;
    companyName: string;
    personaType: PersonaType;
    agentNamesRaw: string;
    agentGenders: Record<string, string>;
    voiceGender?: string;
    voiceId: string;
    voiceName: string;
    provider: string;
    goal: string;
    schedule: CampaignCallingSchedule;
    briefDraft: CampaignBriefDraft;
    leadFields: CampaignLeadField[] | null;
    hadFile: boolean;
    /**
     * Set once `dashboardApi.createCampaign` succeeds, so a later failure (in
     * lead-field save or knowledge upload) can retry against the same row
     * instead of creating a duplicate. Persisting it here means that retry
     * still works after a real navigation away and back, not just in-place.
     */
    createdCampaignId: string | null;
};

function readWizardDraft(key: string | undefined): Partial<CampaignWizardDraft> | null {
    if (!key || typeof window === "undefined") return null;
    try {
        const raw = window.sessionStorage.getItem(key);
        if (!raw) return null;
        const parsed: unknown = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? (parsed as Partial<CampaignWizardDraft>) : null;
    } catch {
        return null;
    }
}

function clearWizardDraft(key: string | undefined): void {
    if (!key || typeof window === "undefined") return;
    try {
        window.sessionStorage.removeItem(key);
    } catch {
        // Storage unavailable: nothing was kept.
    }
}

/** A kept draft worth announcing: something the user typed or reached. */
function wizardDraftHasContent(draft: Partial<CampaignWizardDraft> | null): boolean {
    return Boolean(draft && (
        draft.name?.trim() || draft.companyName?.trim() || draft.agentNamesRaw?.trim() || draft.goal?.trim()
        || draft.createdCampaignId || (draft.step ?? 0) > 0
    ));
}

function writeWizardDraft(key: string | undefined, draft: CampaignWizardDraft): void {
    if (!key || typeof window === "undefined") return;
    try {
        window.sessionStorage.setItem(key, JSON.stringify(draft));
    } catch {
        // Storage unavailable or over quota — degrade to the pre-existing,
        // non-persisted behaviour instead of failing the form.
    }
}

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const STEPS = ["Basics", "Knowledge", "Review"] as const;

/** The step an address names (`?step=1`), or the first step. */
function stepFromAddress(value: string | null | undefined): number {
    const step = Number(value);
    return Number.isInteger(step) && step >= 0 && step < STEPS.length ? step : 0;
}

/** The current address with `step` set (or removed for the first step). */
function addressForStep(step: number): string {
    const url = new URL(window.location.href);
    if (step > 0) url.searchParams.set("step", String(step));
    else url.searchParams.delete("step");
    return `${url.pathname}${url.search}${url.hash}`;
}

function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export type CampaignWizardProps = {
    afterCreateHref?: (campaignId: string) => string;
    /**
     * 2026-09-09: an inbound campaign is created with this exact wizard — same
     * persona, names, brief, guidance, voice, knowledge, contact fields and
     * review. Only the outbound dialling schedule is omitted (inbound hours
     * live on the number-and-routing step) and the row is born inbound.
     */
    direction?: "outbound" | "inbound";
    /**
     * When set, every field below is mirrored to sessionStorage under this
     * key so a real navigation away and back (not just an in-place retry)
     * does not lose step-1 data. Owned and generated by the caller — see
     * `app/inbound-campaigns/new/page.tsx` and `app/campaigns/new/page.tsx`.
     * An outbound draft is removed once its campaign is created; an inbound
     * one belongs to the two-step inbound flow, which removes it.
     */
    draftStorageKey?: string;
    /** Offered when a kept draft came back; the caller discards it and remounts. */
    onStartOver?: () => void;
};

export function CampaignWizard({ afterCreateHref, direction = "outbound", draftStorageKey, onStartOver }: CampaignWizardProps = {}) {
    const router = useRouter();
    const inbound = direction === "inbound";

    // Read once on mount; re-renders never re-read this, only re-write it.
    const [restoredDraft] = useState<Partial<CampaignWizardDraft> | null>(() => readWizardDraft(draftStorageKey));

    const [step, setStep] = useState(() => {
        const restoredStep = restoredDraft?.step;
        return typeof restoredStep === "number" && restoredStep >= 0 && restoredStep <= 2 ? restoredStep : 0;
    });
    const [error, setError] = useState<string | null>(null);

    // Step 1 — basics
    const [voiceSelection, setVoiceSelection] = useState<CampaignVoiceSettingsValue>(restoredDraft?.voiceSelection ?? {});
    const isRealtime = voiceSelection.pipeline_mode === "realtime";
    const [name, setName] = useState(() => (typeof restoredDraft?.name === "string" ? restoredDraft.name : ""));
    const [companyName, setCompanyName] = useState(() => (typeof restoredDraft?.companyName === "string" ? restoredDraft.companyName : ""));
    const [personaType, setPersonaType] = useState<PersonaType>(() => restoredDraft?.personaType ?? "lead_gen");
    const [agentNamesRaw, setAgentNamesRaw] = useState(() => (typeof restoredDraft?.agentNamesRaw === "string" ? restoredDraft.agentNamesRaw : ""));
    const [agentGenders, setAgentGenders] = useState<Record<string, string>>(() => (
        restoredDraft?.agentGenders && typeof restoredDraft.agentGenders === "object" ? restoredDraft.agentGenders : {}
    ));
    const [voiceGender, setVoiceGender] = useState<string | undefined>(() => restoredDraft?.voiceGender);
    const [voiceId, setVoiceId] = useState(() => (typeof restoredDraft?.voiceId === "string" ? restoredDraft.voiceId : ""));
    const [voiceName, setVoiceName] = useState(() => (typeof restoredDraft?.voiceName === "string" ? restoredDraft.voiceName : ""));
    const [provider, setProvider] = useState(() => (typeof restoredDraft?.provider === "string" ? restoredDraft.provider : ""));
    const [goal, setGoal] = useState(() => (typeof restoredDraft?.goal === "string" ? restoredDraft.goal : ""));
    const [schedule, setSchedule] = useState<CampaignCallingSchedule>(() => (
        restoredDraft?.schedule && typeof restoredDraft.schedule === "object" ? restoredDraft.schedule : defaultCallingSchedule()
    ));
    const [briefDraft, setBriefDraft] = useState<CampaignBriefDraft>(() => ({
        ...EMPTY_CAMPAIGN_BRIEF_DRAFT,
        ...(restoredDraft?.briefDraft && typeof restoredDraft.briefDraft === "object" ? restoredDraft.briefDraft : {}),
    }));

    // Step 2 — knowledge. The File itself never survives a restore (it can't
    // be serialized); `fileRestoreNotice` tells the user to re-attach it.
    const [file, setFile] = useState<File | null>(null);
    const [fileRestoreNotice, setFileRestoreNotice] = useState<boolean>(() => Boolean(restoredDraft?.hadFile));
    const fileRef = useRef<HTMLInputElement>(null);

    // Step 3 — preview + submit
    const [preview, setPreview] = useState<{
        system_prompt: string;
        greeting: string;
        prompt_chars: number;
        layers: Array<{ key: string; label: string; content: string }>;
    } | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [createdCampaignId, setCreatedCampaignId] = useState<string | null>(() => (
        typeof restoredDraft?.createdCampaignId === "string" ? restoredDraft.createdCampaignId : null
    ));
    const leadFields = useCampaignLeadFieldDraft();

    // Applies the restored lead-field selection once; `restoredDraft` itself
    // never changes after mount, so this never fires again.
    useEffect(() => {
        if (restoredDraft?.leadFields) {
            leadFields.setFields(restoredDraft.leadFields);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps -- one-time restore, deliberately not re-run when leadFields/restoredDraft identity changes
    }, []);

    // Set once an outbound campaign is created, so no later render writes
    // the finished draft back.
    const draftFinished = useRef(false);

    // Mirrors the current draft to sessionStorage on every change. No-op
    // without `draftStorageKey`, and never runs on the failure path
    // specifically — it just tracks whatever is currently in these fields,
    // the same way it already did before this was wired to storage.
    useEffect(() => {
        if (draftFinished.current) return;
        writeWizardDraft(draftStorageKey, {
            voiceSelection,
            step,
            name,
            companyName,
            personaType,
            agentNamesRaw,
            agentGenders,
            voiceGender,
            voiceId,
            voiceName,
            provider,
            goal,
            schedule,
            briefDraft,
            leadFields: leadFields.fields,
            hadFile: Boolean(file),
            createdCampaignId,
        });
    }, [voiceSelection, draftStorageKey, step, name, companyName, personaType, agentNamesRaw, agentGenders, voiceGender, voiceId, voiceName, provider, goal, schedule, briefDraft, leadFields.fields, file, createdCampaignId]);

    const agentNames = useMemo(() => parseAgentNames(agentNamesRaw), [agentNamesRaw]);
    const requiredLeadFields = leadFields.fields
        .filter((field) => field.is_required && field.agent_visible)
        .map((field) => ({ field_key: field.field_key, label: field.label }));
    const campaignBrief = buildCampaignBrief({
        draft: briefDraft,
        brand: companyName,
        representativeNames: agentNames,
        requiredLeadFields,
    });
    const briefError = campaignBriefValidation(briefDraft);
    const guidance = guidanceBudgetStatus(campaignBriefGuidanceText(goal, campaignBrief));
    const basicsValid = Boolean(
        name.trim()
        && companyName.trim()
        && agentNames.length >= 1
        && !representativeNameTooLong(agentNames)
        && (isRealtime ? voiceSelection.realtime_voice : voiceId)
        && (isRealtime || !briefError)
        && (isRealtime || guidance.valid)
        && (inbound || !callingHoursInverted(schedule)),
    );

    const onPickFile = (f: File | null) => {
        setError(null);
        if (!f) { setFile(null); return; }
        const lower = f.name.toLowerCase();
        if (!(lower.endsWith(".md") || lower.endsWith(".txt"))) {
            setError("Please choose a .md or .txt file.");
            return;
        }
        if (f.size > MAX_UPLOAD_BYTES) {
            setError(`File too large (max ${fmtBytes(MAX_UPLOAD_BYTES)}).`);
            return;
        }
        setFile(f);
        setFileRestoreNotice(false);
    };

    // QA F1-22 (QA-UI-042): each step has its own history entry (?step=), so
    // browser Back and Forward move between steps instead of leaving the
    // wizard. Outside the app router (no search params) steps stay plain state.
    const searchParams = useSearchParams();
    const routed = searchParams !== null;
    const addressStep = searchParams ? stepFromAddress(searchParams.get("step")) : null;
    const [seenAddressStep, setSeenAddressStep] = useState(addressStep);
    if (addressStep !== null && addressStep !== seenAddressStep) {
        // The address moved (browser Back/Forward): follow it, but never to a
        // step the basics do not allow yet.
        setSeenAddressStep(addressStep);
        setStep(addressStep > 0 && !basicsValid ? 0 : addressStep);
    }
    // Entries this wizard pushed, so its own Back steps back through them.
    const pushedSteps = useRef(0);
    const goToStep = (next: number) => {
        if (!routed) {
            setStep(next);
            return;
        }
        if (next < step && pushedSteps.current > 0) {
            pushedSteps.current -= 1;
            window.history.back(); // the address change brings the step back
            return;
        }
        if (next > step) {
            window.history.pushState(null, "", addressForStep(next));
            pushedSteps.current += 1;
        } else {
            window.history.replaceState(null, "", addressForStep(next));
        }
        setSeenAddressStep(next);
        setStep(next);
    };
    // The address names the step shown: a restored draft's step, or the
    // first step when the address asked for one the basics do not allow.
    useEffect(() => {
        if (!routed) return;
        if (stepFromAddress(new URLSearchParams(window.location.search).get("step")) !== step) {
            window.history.replaceState(null, "", addressForStep(step));
        }
    }, [routed, step]);

    const goToReview = () => {
        if (!isRealtime && (briefError || !guidance.valid)) {
            setError(briefError ?? guidance.message ?? "Campaign guidance is invalid.");
            return;
        }
        goToStep(2);
    };

    // The review step reads a fresh preview each time it is entered: by Next,
    // by browser Forward, or from a restored draft.
    const loadPreview = async (isCurrent: () => boolean) => {
        setPreviewLoading(true);
        setPreview(null);
        try {
            const res = await dashboardApi.previewCampaignPrompt({
                ...voiceSelection,
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_name: agentNames[0] ?? "Alex",
                campaign_slots: {},
                additional_instructions: goal.trim() || undefined,
                campaign_brief: campaignBrief,
                direction: inbound ? "inbound" : "outbound",
                knowledge_driven: true,
            });
            if (!isCurrent()) return;
            setPreview({
                system_prompt: res.system_prompt,
                greeting: res.greeting,
                prompt_chars: res.prompt_chars,
                layers: res.layers,
            });
        } catch (err) {
            if (isCurrent()) setError(err instanceof Error ? err.message : "Couldn't build a preview");
        } finally {
            if (isCurrent()) setPreviewLoading(false);
        }
    };
    useEffect(() => {
        if (step !== 2) return;
        let current = true;
        // Entering the review step starts its preview read (an external fetch).
        // eslint-disable-next-line react-hooks/set-state-in-effect -- the read sets its own loading state as it starts
        void loadPreview(() => current);
        return () => { current = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps -- once per entry into the review step, with the inputs as they are then
    }, [step]);

    const onCreate = async () => {
        if (!isRealtime && (briefError || !guidance.valid)) {
            setError(briefError ?? guidance.message ?? "Campaign guidance is invalid.");
            return;
        }
        if (leadFields.isLoading || leadFields.isError) {
            setError("Contact-field settings must load successfully before this campaign can be created.");
            return;
        }
        setSubmitting(true);
        setError(null);
        let campaignId = createdCampaignId;
        let updateFailed = false;
        try {
            const body = {
                ...voiceSelection,
                name: name.trim(),
                description: undefined,
                system_prompt: goal.trim(),      // additional instructions
                voice_id: voiceId,
                tts_provider: provider || undefined,   // per-campaign engine
                goal: goal.trim() || undefined,
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_names: agentNames,
                agent_name_genders: pruneGenders(agentGenders, agentNames),
                campaign_slots: {},
                campaign_brief: campaignBrief,
                knowledge_driven: true,
            };
            if (!campaignId) {
                const { campaign } = await dashboardApi.createCampaign({
                    ...body,
                    ...(inbound ? { direction: "inbound" as const } : { calling_schedule: callingSchedulePayload(schedule) }),
                });
                campaignId = campaign.id;
                setCreatedCampaignId(campaign.id);
            } else {
                // A retry after a partial failure: the user may have changed step 1
                // since the first attempt, so send the current values too.
                updateFailed = true;
                await dashboardApi.updateCampaign(campaignId, {
                    ...body,
                    ...(inbound ? {} : { calling_schedule: callingSchedulePayload(schedule) }),
                });
                updateFailed = false;
            }
            await leadDetailsApi.setCampaignFields(campaignId, leadFields.fields);
            // The outbound campaign exists: its draft must not come back.
            if (!inbound) {
                draftFinished.current = true;
                clearWizardDraft(draftStorageKey);
            }
            if (file) {
                try {
                    await api.uploadCampaignKnowledge(campaignId, file);
                } catch {
                    router.push(`/campaigns/${campaignId}?knowledge_error=1`);
                    return;
                }
            }
            router.push(afterCreateHref ? afterCreateHref(campaignId) : `/campaigns/${campaignId}`);
        } catch (err) {
            const detail = err instanceof Error ? err.message : "Failed to create campaign";
            setError(
                campaignId && !updateFailed
                    ? `Campaign details were created, but its contact-field policy was not saved. Retry to finish without creating a duplicate. ${detail}`
                    : detail,
            );
            setSubmitting(false);
        }
    };

    return (
        <div className="mx-auto max-w-3xl">
            {/* stepper */}
            <ol className="mb-6 flex items-center gap-2">
                {STEPS.map((label, i) => (
                    <li key={label} className="flex flex-1 items-center gap-2">
                        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                            i < step ? "bg-emerald-500 text-white"
                                : i === step ? "bg-emerald-100 text-emerald-700 ring-2 ring-emerald-500 dark:bg-emerald-950 dark:text-emerald-300"
                                    : "bg-gray-100 text-gray-400 dark:bg-white/10"}`}>
                            {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
                        </div>
                        <span className={`text-sm font-medium ${i === step ? "text-gray-900 dark:text-zinc-100" : "text-muted-foreground"}`}>
                            {label}
                        </span>
                        {i < STEPS.length - 1 && <div className="h-px flex-1 bg-gray-200 dark:bg-white/10" />}
                    </li>
                ))}
            </ol>

            {onStartOver && wizardDraftHasContent(restoredDraft) && <DraftRestoredNotice onStartOver={onStartOver} />}

            {error && (
                <div role="alert" className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
                    {error}
                </div>
            )}

            <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 p-5 shadow-sm">
                {step === 0 && (
                    <div className="space-y-5">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div>
                                <Label htmlFor="cw-name">Campaign name</Label>
                                <Input id="cw-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Q3 Web Dev Outreach" className="mt-1" />
                            </div>
                            <div>
                                <Label htmlFor="cw-company">Brand / company name</Label>
                                <Input id="cw-company" value={companyName} maxLength={MAX_BRIEF_NAME_LENGTH} onChange={(e) => setCompanyName(e.target.value)} placeholder="Talk-Lee" className="mt-1" />
                            </div>
                        </div>

                        <div>
                            <Label>Persona</Label>
                            <div className="mt-1 grid gap-2 sm:grid-cols-3">
                                {PERSONAS.map((p) => (
                                    <button
                                        type="button"
                                        key={p.value}
                                        onClick={() => setPersonaType(p.value)}
                                        className={`rounded-xl border p-3 text-left transition ${
                                            personaType === p.value
                                                ? "border-emerald-500 ring-1 ring-emerald-500 bg-emerald-50 dark:bg-emerald-950/40"
                                                : "border-gray-200 dark:border-white/10 hover:border-gray-300"}`}
                                    >
                                        <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{p.title}</div>
                                        <div className="mt-0.5 text-xs text-muted-foreground">{p.summary}</div>
                                    </button>
                                ))}
                            </div>
                        </div>

                        <div>
                            <Label htmlFor="cw-agents">Representative names</Label>
                            <Input id="cw-agents" value={agentNamesRaw} onChange={(e) => setAgentNamesRaw(e.target.value)} placeholder="Alex, Jordan, Sam" className="mt-1" />
                            <p className="mt-1 text-xs text-muted-foreground">
                                1–3 names, comma-separated. The agent introduces itself with one (rotated per call).
                                {agentNames.length > 0 && <span className="ml-1 text-emerald-600 dark:text-emerald-400">{agentNames.length} name{agentNames.length > 1 ? "s" : ""}.</span>}
                            </p>
                            {representativeNameTooLong(agentNames) && <p role="alert" className="mt-1 text-xs text-destructive">The first representative name must be 120 characters or fewer.</p>}
                            <AgentNameGender names={agentNames} value={agentGenders} onChange={setAgentGenders} voiceGender={voiceGender} />
                        </div>

                        <CampaignVoiceSettings value={voiceSelection} onChange={setVoiceSelection} />
                        <div hidden={isRealtime}>
                        <CampaignBriefFields
                            idPrefix="wizard-campaign-brief"
                            value={briefDraft}
                            onChange={setBriefDraft}
                            requiredLeadFields={requiredLeadFields}
                            disabled={submitting}
                        />

                        <div>
                            <div className="flex items-baseline justify-between gap-3">
                                <Label htmlFor="cw-goal">Additional campaign guidance</Label>
                                <span className={`text-xs tabular-nums ${guidance.overBudget ? "font-semibold text-destructive" : "text-muted-foreground"}`}>
                                    {guidance.chars.toLocaleString()} / {guidance.budget.toLocaleString()}
                                </span>
                            </div>
                            <textarea
                                id="cw-goal" value={goal} onChange={(e) => setGoal(e.target.value)} rows={2}
                                placeholder="Optional nuance that is not already captured in the structured brief. Put business facts in Company knowledge."
                                aria-invalid={guidance.overBudget || undefined}
                                className="mt-1 w-full resize-y rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                            />
                            <p className={`mt-1 text-xs ${guidance.overBudget ? "text-destructive" : "text-muted-foreground"}`}>
                                {guidance.overBudget
                                    ? guidance.message
                                    : "The counter includes the structured brief and this freeform layer. Nothing is trimmed automatically."}
                            </p>
                        </div>

                        <VoiceProviderPicker
                            personaType={personaType}
                            voiceId={voiceId}
                            onVoiceChange={(id, nm) => { setVoiceId(id); setVoiceName(nm ?? ""); }}
                            onProviderChange={setProvider}
                            onVoiceGenderChange={setVoiceGender}
                        />
                        </div>

                        {inbound ? null : (
                            <div className="rounded-lg border border-border bg-background/50 p-4">
                                <CallingScheduleEditor value={schedule} onChange={setSchedule} />
                            </div>
                        )}

                        <div className="flex justify-end pt-1">
                            <Button onClick={() => { setError(null); goToStep(1); }} disabled={!basicsValid}>
                                Next: Knowledge <ArrowRight className="h-4 w-4" />
                            </Button>
                        </div>
                    </div>
                )}

                {step === 1 && (
                    <div className="space-y-5">
                        <div className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
                            <BookOpen className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" />
                            <div className="text-sm text-gray-700 dark:text-zinc-300">
                                <p className="font-medium text-gray-900 dark:text-zinc-100">Upload your knowledge</p>
                                <p className="mt-0.5">
                                    A Markdown or text file with everything the agent should know — services, pricing,
                                    FAQs, policies. We parse it into sections, write a natural spoken answer for each,
                                    and the agent uses it to answer callers. No need to fill in fields one by one.
                                </p>
                            </div>
                        </div>

                        {fileRestoreNotice ? (
                            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                                Your previously selected knowledge file could not be restored after navigating away — please re-attach it.
                            </div>
                        ) : null}

                        <input
                            ref={fileRef} type="file" accept=".md,.txt,text/markdown,text/plain" className="hidden"
                            onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
                        />
                        {file ? (
                            <div className="flex items-center gap-3 rounded-lg border border-gray-200 dark:border-white/10 px-3 py-2.5">
                                <FileText className="h-5 w-5 text-emerald-500" />
                                <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm font-medium text-gray-900 dark:text-zinc-100">{file.name}</div>
                                    <div className="text-xs text-muted-foreground">{fmtBytes(file.size)}</div>
                                </div>
                                <button type="button" onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }}
                                    className="rounded p-1 text-muted-foreground hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-950/40">
                                    <X className="h-4 w-4" />
                                </button>
                            </div>
                        ) : (
                            <button
                                type="button" onClick={() => fileRef.current?.click()}
                                className="flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-300 dark:border-white/15 px-4 py-10 text-center hover:border-emerald-400 hover:bg-emerald-50/40 dark:hover:bg-emerald-950/20"
                            >
                                <Upload className="h-7 w-7 text-muted-foreground" />
                                <span className="text-sm font-medium text-gray-900 dark:text-zinc-100">Choose a .md or .txt file</span>
                                <span className="text-xs text-muted-foreground">up to {fmtBytes(MAX_UPLOAD_BYTES)}</span>
                            </button>
                        )}

                        <p className="text-xs text-muted-foreground">
                            You can skip this and add knowledge later from the campaign page — but the agent
                            will only have its persona to work from until you do.
                        </p>

                        <div className="border-t border-gray-200 pt-5 dark:border-white/10">
                            <CampaignLeadFieldsPicker
                                specs={leadFields.specs}
                                value={leadFields.fields}
                                onChange={leadFields.setFields}
                                isLoading={leadFields.isLoading}
                                error={leadFields.isError ? leadFields.error : undefined}
                                onRetry={leadFields.retry}
                                disabled={submitting}
                            />
                        </div>

                        <div className="flex justify-between pt-1">
                            <Button variant="ghost" onClick={() => goToStep(0)}><ArrowLeft className="h-4 w-4" /> Back</Button>
                            <Button onClick={goToReview}>Next: Review <ArrowRight className="h-4 w-4" /></Button>
                        </div>
                    </div>
                )}

                {step === 2 && (
                    <div className="space-y-5">
                        <div className="grid gap-3 sm:grid-cols-2 text-sm">
                            <SummaryRow label="Campaign" value={name} />
                            <SummaryRow label="Brand / company name" value={companyName} />
                            <SummaryRow label="Persona" value={PERSONAS.find((p) => p.value === personaType)?.title ?? personaType} />
                            <SummaryRow label="Representative names" value={agentNames.join(", ")} />
                            <SummaryRow label="Opening objective" value={briefDraft.opening_objective} />
                            <SummaryRow label="Decision role" value={briefDraft.decision_maker_role || "— any informed contact —"} />
                            <SummaryRow label="Next actions" value={briefDraft.approved_next_actions.length ? briefDraft.approved_next_actions.join(", ").replaceAll("_", " ") : "— none approved —"} />
                            <SummaryRow label="Objections" value={`${briefDraft.max_objection_attempts} attempts maximum`} />
                            <SummaryRow label="Voice" value={isRealtime ? `GPT Realtime (${voiceSelection.realtime_voice})` : voiceName ? `${voiceName}${provider ? ` (${provider})` : ""}` : voiceId} />
                            <SummaryRow label="Knowledge" value={file ? file.name : "— none —"} />
                            <SummaryRow label="Lead fields" value={leadFields.fields.length ? `${leadFields.fields.length} selected` : "— none —"} />
                        </div>

                        <div>
                            <Label>{inbound ? "How the agent will answer (default opening)" : "How the agent will open"}</Label>
                            {inbound ? (
                                <p className="mt-1 text-xs text-muted-foreground">
                                    Your exact greeting for this line is set in step 2 (Number &amp; routing) and overrides this default.
                                </p>
                            ) : null}
                            <div className="mt-1 rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 px-3 py-2 text-sm italic text-gray-700 dark:text-zinc-300 min-h-[2.5rem]">
                                {previewLoading ? <span className="inline-flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> building preview…</span>
                                    : preview?.greeting || "—"}
                            </div>
                        </div>

                        {preview?.layers.length ? (
                            <PromptLayerPreview
                                layers={preview.layers}
                                promptChars={preview.prompt_chars}
                                headingId="wizard-prompt-layers-heading"
                            />
                        ) : preview?.system_prompt ? (
                            <p className="text-sm text-muted-foreground">Prompt layers were not returned by the server.</p>
                        ) : null}

                        <div className="flex justify-between pt-1">
                            <Button variant="ghost" onClick={() => goToStep(1)} disabled={submitting}><ArrowLeft className="h-4 w-4" /> Back</Button>
                            <Button onClick={onCreate} disabled={submitting || leadFields.isLoading || leadFields.isError}>
                                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                                {submitting ? "Creating…" : "Create campaign"}
                            </Button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
    return (
        <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] items-start gap-2">
            <span className="text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
            <span className="break-words font-medium text-gray-900 dark:text-zinc-100">{value || "—"}</span>
        </div>
    );
}

export default CampaignWizard;
