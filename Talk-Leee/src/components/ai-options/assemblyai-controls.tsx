"use client";

import { useId } from "react";
import { Select } from "@/components/ui/select";
import { ASSEMBLYAI_MODE_OPTIONS, ASSEMBLYAI_PII_POLICIES, type AssemblyAISettings } from "@/lib/assemblyai-settings";

const inputClass = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground disabled:opacity-50";

function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string) => React.ReactNode }) {
    const id = useId();
    return <div className="space-y-1.5"><label htmlFor={id} className="block text-sm font-medium">{label}</label>{children(id)}{hint && <p className="text-xs text-muted-foreground">{hint}</p>}</div>;
}

function NumberOverride({ label, hint, value, onChange, min, max, step = 1, disabled = false }: {
    label: string; hint: string; value?: number | null; onChange: (value: number | null) => void;
    min: number; max?: number; step?: number; disabled?: boolean;
}) {
    return <Field label={label} hint={hint}>{(id) => <input id={id} type="number" className={inputClass} placeholder="Use provider default" value={value ?? ""} min={min} max={max} step={step} disabled={disabled}
        onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} />}</Field>;
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (checked: boolean) => void }) {
    return <div><label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" className="accent-emerald-500" checked={checked} onChange={(event) => onChange(event.target.checked)} />{label}</label><p className="mt-1 text-xs text-muted-foreground">{hint}</p></div>;
}

export function AssemblyAIControls({ value, onChange }: { value?: AssemblyAISettings | null; onChange: (value: AssemblyAISettings) => void }) {
    const settings = value ?? {};
    const set = <K extends keyof AssemblyAISettings>(field: K, next: AssemblyAISettings[K]) => onChange({ ...settings, [field]: next });
    const mode = settings.mode ?? "balanced";
    const partials = settings.include_partial_turns == null ? "auto" : String(settings.include_partial_turns);

    return <div className="space-y-5" aria-label="AssemblyAI 3.6 Pro settings">
        <Field label="Transcription language" hint="English is fixed for AssemblyAI calls, including calls started from campaigns.">{(id) => <input id={id} className={inputClass} value="English (en)" readOnly disabled />}</Field>
        <Field label="AssemblyAI mode" hint={ASSEMBLYAI_MODE_OPTIONS.find((option) => option.value === mode)?.description}>{(id) => <Select id={id} ariaLabel="AssemblyAI mode" value={mode} onChange={(next) => set("mode", next as AssemblyAISettings["mode"])}>
            {ASSEMBLYAI_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </Select>}</Field>
        <p className="text-xs text-muted-foreground">Changes apply to new calls after Save Config. Leave advanced overrides blank to keep the selected mode&apos;s defaults.</p>

        <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-semibold">Recognition and conversation context</summary>
            <div className="mt-4 space-y-4">
                <Field label="Audio context prompt" hint="Optional. Describe the call topic or unusual vocabulary in a short paragraph. Start without a prompt; this is separate from your agent instructions. Up to 1,750 characters.">{(id) => <textarea id={id} className={inputClass} rows={3} maxLength={1750} value={settings.prompt ?? ""} onChange={(event) => set("prompt", event.target.value)} placeholder="English appointment booking calls for a dental clinic." />}</Field>
                <Field label="Keyterms" hint="One name or specialist term per line. Maximum 100 terms, 50 characters each. Avoid generic words and letter lists.">{(id) => <textarea id={id} className={inputClass} rows={4} value={(settings.keyterms_prompt ?? []).join("\n")} onChange={(event) => set("keyterms_prompt", event.target.value.split("\n"))} onBlur={(event) => set("keyterms_prompt", event.target.value.split("\n").map((term) => term.trim()).filter(Boolean))} placeholder={"Talky\nAcme Dental"} />}</Field>
                <Toggle label="Use agent replies as recognition context" checked={settings.auto_agent_context ?? true} onChange={(next) => set("auto_agent_context", next)} hint="Shares the agent's spoken reply with AssemblyAI to help recognize the caller's next answer, including names and email addresses." />
                <NumberOverride label="Previous conversation turns" hint="Provider default: 5. Choose 0 to disable conversation history, or up to 100 turns." min={0} max={100} value={settings.previous_context_n_turns} onChange={(next) => set("previous_context_n_turns", next)} />
            </div>
        </details>

        <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-semibold">Turn detection and interruptions</summary>
            <div className="mt-4 space-y-4">
                <NumberOverride label="Minimum turn silence (ms)" hint="Wait before checking whether an answer is complete. Longer pauses can help with spelling and phone numbers. Range: 50–10,000 ms." min={50} max={10000} value={settings.min_turn_silence} onChange={(next) => set("min_turn_silence", next)} />
                <NumberOverride label="Maximum turn silence (ms)" hint="Force the turn to finish after this pause. Must be at least the minimum silence when both are set." min={1} value={settings.max_turn_silence} onChange={(next) => set("max_turn_silence", next)} />
                <NumberOverride label="Interruption delay (ms)" hint="Additional delay before the first partial transcript, from 0–1,000 ms. AssemblyAI adds 256 ms to this value." min={0} max={1000} value={settings.interruption_delay} onChange={(next) => set("interruption_delay", next)} />
                <NumberOverride label="Speech detection threshold" hint="Lower values hear quieter speech; higher values reject more noise. Leave blank for the provider's default, which also depends on speaker labels." min={0} max={1} step={0.01} value={settings.vad_threshold} onChange={(next) => set("vad_threshold", next)} />
                <Field label="Partial transcripts" hint="Auto follows the provider: enabled normally, disabled with PII redaction. The agent uses finalized turns for answers.">{(id) => <Select id={id} ariaLabel="Partial transcripts" value={partials} onChange={(next) => set("include_partial_turns", next === "auto" ? null : next === "true")}><option value="auto">Auto (provider default)</option><option value="true">On</option><option value="false">Off</option></Select>}</Field>
                {settings.redact_pii && settings.include_partial_turns === true && <p role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs">Partial transcripts contain unredacted personal details even when PII redaction is enabled. Use Auto or Off to receive only redacted finals.</p>}
            </div>
        </details>

        <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-semibold">Audio processing and speaker labels</summary>
            <div className="mt-4 space-y-4">
                <p className="text-xs text-muted-foreground">Voice Focus, medical vocabulary and speaker labels may add provider charges.</p>
                <Field label="Voice Focus" hint="Suppress background sound. Near field favors a close microphone; far field supports speakers farther away.">{(id) => <Select id={id} ariaLabel="Voice Focus" value={settings.voice_focus ?? "off"} onChange={(next) => set("voice_focus", next === "off" ? null : next as "near-field" | "far-field")}><option value="off">Off</option><option value="near-field">Near field</option><option value="far-field">Far field</option></Select>}</Field>
                <NumberOverride label="Voice Focus threshold" hint="Provider default: 0.7. Applies only when Voice Focus is enabled." min={0} max={1} step={0.01} disabled={!settings.voice_focus} value={settings.voice_focus_threshold} onChange={(next) => set("voice_focus_threshold", next)} />
                <Field label="Vocabulary domain" hint="Medical mode specializes recognition for clinical terminology.">{(id) => <Select id={id} ariaLabel="Vocabulary domain" value={settings.domain ?? "off"} onChange={(next) => set("domain", next === "off" ? null : "medical-v1")}><option value="off">General</option><option value="medical-v1">Medical</option></Select>}</Field>
                <Toggle label="Speaker labels" hint="Label different voices. Changes the provider's default turn timing; unnecessary for most single-caller conversations." checked={settings.speaker_labels ?? false} onChange={(next) => set("speaker_labels", next)} />
                <fieldset disabled={!settings.speaker_labels} className="space-y-4 disabled:opacity-50" aria-label="Speaker label settings">
                    <NumberOverride label="Maximum speakers" hint="Optional speaker count limit, from 1 to 10." min={1} max={10} value={settings.max_speakers} onChange={(next) => set("max_speakers", next)} />
                    <NumberOverride label="Speaker label revision interval (ms)" hint="Use 0 to disable mid-session revisions or at least 120,000 ms. An end-of-session revision still occurs. Leave blank for the provider default." min={0} value={settings.speaker_labels_revision_interval_ms} onChange={(next) => set("speaker_labels_revision_interval_ms", next)} />
                </fieldset>
            </div>
        </details>

        <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-semibold">Privacy and transcript filtering</summary>
            <div className="mt-4 space-y-4">
                <Toggle label="Redact personal information (PII)" hint="Off by default for lead capture. Redaction can hide names, emails and phone numbers from the agent and lead records. This optional feature may add provider charges." checked={settings.redact_pii ?? false} onChange={(next) => set("redact_pii", next)} />
                {settings.redact_pii && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs">Redacted contact details cannot be recovered for leads from the transcript. Redaction applies to final text, not the call recording.</p>}
                <fieldset disabled={!settings.redact_pii} className="space-y-4 disabled:opacity-50" aria-label="PII redaction settings">
                    <fieldset className="space-y-2">
                        <legend className="text-sm font-medium">PII policies</legend>
                        <p className="text-xs text-muted-foreground">No specific selection means all supported types are redacted. Select individual types to limit redaction.</p>
                        <p className="text-xs font-medium">{settings.redact_pii_policies?.length ? `${settings.redact_pii_policies.length} specific types selected` : "All types (provider default)"}</p>
                        <div className="grid max-h-52 gap-2 overflow-y-auto rounded-lg border border-border p-3 sm:grid-cols-2">
                            {ASSEMBLYAI_PII_POLICIES.map((policy) => <label key={policy} className="flex items-start gap-2 text-xs"><input type="checkbox" className="mt-0.5 accent-emerald-500" checked={settings.redact_pii_policies?.includes(policy) ?? false} onChange={(event) => set("redact_pii_policies", event.target.checked ? [...(settings.redact_pii_policies ?? []), policy] : (settings.redact_pii_policies ?? []).filter((entry) => entry !== policy))} /><span className="capitalize">{policy.replaceAll("_", " ")}</span></label>)}
                        </div>
                        {!!settings.redact_pii_policies?.length && <button type="button" className="text-xs underline" onClick={() => set("redact_pii_policies", [])}>Clear selection and use all types</button>}
                    </fieldset>
                    <Field label="PII replacement" hint="Choose the text used in place of personal information.">{(id) => <Select id={id} disabled={!settings.redact_pii} ariaLabel="PII replacement" value={settings.redact_pii_sub ?? "hash"} onChange={(next) => set("redact_pii_sub", next as "hash" | "entity_name")}><option value="hash">Hash characters (####)</option><option value="entity_name">Entity labels ([EMAIL_ADDRESS])</option></Select>}</Field>
                </fieldset>
                <Toggle label="Filter profanity" hint="Replace recognized profanity in transcripts." checked={settings.filter_profanity ?? false} onChange={(next) => set("filter_profanity", next)} />
            </div>
        </details>

        <details className="rounded-lg border border-border p-3">
            <summary className="cursor-pointer text-sm font-semibold">Connection and diagnostics</summary>
            <div className="mt-4 space-y-4">
                <Field label="AssemblyAI region" hint="Global uses the nearest AssemblyAI endpoint. Choose a regional endpoint when required for data residency.">{(id) => <Select id={id} ariaLabel="AssemblyAI region" value={settings.region ?? "global"} onChange={(next) => set("region", next as AssemblyAISettings["region"])}><option value="global">Global (nearest)</option><option value="us">United States</option><option value="eu">European Union</option></Select>}</Field>
                <Toggle label="Session heartbeat" hint="Receive provider session-health updates while the call runs." checked={settings.session_heartbeat ?? true} onChange={(next) => set("session_heartbeat", next)} />
                <NumberOverride label="Audio inactivity timeout (seconds)" hint="Optional provider disconnect when no audio arrives. Range: 5–3,600 seconds. This does not set how long the caller may pause." min={5} max={3600} value={settings.inactivity_timeout} onChange={(next) => set("inactivity_timeout", next)} />
                <Toggle label="Report detected language" hint="Adds language metadata for diagnostics. The selected transcription language remains English." checked={settings.language_detection ?? false} onChange={(next) => set("language_detection", next)} />
            </div>
        </details>
    </div>;
}
