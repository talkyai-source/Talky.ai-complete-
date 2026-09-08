"use client";

import { useRef } from "react";
import { BookOpen, FileText, Upload, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { AgentNameGender, pruneGenders } from "@/components/campaigns/agent-name-gender";
import { VoiceProviderPicker } from "@/components/campaigns/voice-provider-picker";
import { PERSONAS, parseAgentNames } from "@/lib/campaign-personas";
import type { PersonaType } from "@/lib/dashboard-api";
import type { InboundAgentInput } from "@/lib/inbound-api";

/**
 * The AI agent an inbound campaign owns (2026-09-09).
 *
 * Same fields, same components and the same validation surface as the
 * outbound campaign creator (`campaign-wizard.tsx` step 1), so creating an
 * inbound campaign feels like creating an outbound one — and nothing here
 * points at, reuses or converts an outbound campaign.
 */
export type InboundAgentErrors = Partial<Record<"company_name" | "agent_names" | "voice_id" | "goal", string>>;

export const INBOUND_KNOWLEDGE_MAX_BYTES = 10 * 1024 * 1024;

export function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function InboundAgentSection({
    value,
    onChange,
    errors,
    disabled,
}: {
    value: InboundAgentInput;
    onChange: (next: InboundAgentInput) => void;
    errors?: InboundAgentErrors;
    disabled?: boolean;
}) {
    const agentNames = parseAgentNames(value.agent_names_raw ?? value.agent_names.join(", "));
    const set = (patch: Partial<InboundAgentInput>) => onChange({ ...value, ...patch });

    return (
        <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                    <Label htmlFor="inbound-agent-company">Brand / company name</Label>
                    <Input
                        id="inbound-agent-company"
                        value={value.company_name}
                        onChange={(e) => set({ company_name: e.target.value })}
                        placeholder="Dojo"
                        disabled={disabled}
                        aria-invalid={Boolean(errors?.company_name)}
                    />
                    {errors?.company_name ? <p className="text-xs font-medium text-destructive">{errors.company_name}</p> : null}
                </div>
                <div className="space-y-2">
                    <Label htmlFor="inbound-agent-names">Agent names</Label>
                    <Input
                        id="inbound-agent-names"
                        value={value.agent_names_raw ?? value.agent_names.join(", ")}
                        onChange={(e) => {
                            const raw = e.target.value;
                            const names = parseAgentNames(raw);
                            set({ agent_names_raw: raw, agent_names: names, agent_name_genders: pruneGenders(value.agent_name_genders ?? {}, names) });
                        }}
                        placeholder="Sarah, Alex"
                        disabled={disabled}
                        aria-invalid={Boolean(errors?.agent_names)}
                    />
                    <p className="text-xs text-muted-foreground">
                        1–3 names, comma-separated. The agent introduces itself with one (rotated per call).
                    </p>
                    {errors?.agent_names ? <p className="text-xs font-medium text-destructive">{errors.agent_names}</p> : null}
                    <AgentNameGender
                        names={agentNames}
                        value={value.agent_name_genders ?? {}}
                        onChange={(genders) => set({ agent_name_genders: genders })}
                        voiceGender={value.voice_gender}
                    />
                </div>
            </div>

            <div>
                <Label>Agent style</Label>
                <div className="mt-1 grid gap-2 sm:grid-cols-3">
                    {PERSONAS.map((p) => (
                        <button
                            type="button"
                            key={p.value}
                            disabled={disabled}
                            onClick={() => set({ persona_type: p.value as PersonaType })}
                            aria-pressed={value.persona_type === p.value}
                            className={`rounded-xl border p-3 text-left transition ${
                                value.persona_type === p.value
                                    ? "border-emerald-500 ring-1 ring-emerald-500 bg-emerald-50 dark:bg-emerald-950/40"
                                    : "border-gray-200 dark:border-white/10 hover:border-gray-300"}`}
                        >
                            <div className="text-sm font-semibold text-gray-900 dark:text-zinc-100">{p.title}</div>
                            <div className="mt-0.5 text-xs text-muted-foreground">{p.summary}</div>
                        </button>
                    ))}
                </div>
            </div>

            <div className="space-y-2">
                <Label>Voice</Label>
                <VoiceProviderPicker
                    personaType={value.persona_type}
                    voiceId={value.voice_id}
                    initialProvider={value.tts_provider ?? null}
                    onVoiceChange={(voiceId) => set({ voice_id: voiceId })}
                    onProviderChange={(provider) => set({ tts_provider: provider || null })}
                    onVoiceGenderChange={(gender) => set({ voice_gender: gender })}
                />
                {errors?.voice_id ? <p className="text-xs font-medium text-destructive">{errors.voice_id}</p> : null}
            </div>

            <div className="space-y-2">
                <Label htmlFor="inbound-agent-goal">What should this agent do for callers?</Label>
                <textarea
                    id="inbound-agent-goal"
                    value={value.goal ?? ""}
                    onChange={(e) => set({ goal: e.target.value })}
                    rows={3}
                    disabled={disabled}
                    placeholder="Answer questions about our card terminals, take the caller's name and business, and book a callback with the sales team."
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                />
                <p className="text-xs text-muted-foreground">
                    Plain language. This becomes the agent&apos;s own guidance, exactly as for an outbound campaign.
                </p>
                {errors?.goal ? <p className="text-xs font-medium text-destructive">{errors.goal}</p> : null}
            </div>
        </div>
    );
}

export function InboundKnowledgeUpload({
    file,
    onChange,
    disabled,
}: {
    file: File | null;
    onChange: (file: File | null, error?: string) => void;
    disabled?: boolean;
}) {
    const fileRef = useRef<HTMLInputElement | null>(null);
    const pick = (next: File | null) => {
        if (next && next.size > INBOUND_KNOWLEDGE_MAX_BYTES) {
            onChange(null, `That file is ${fmtBytes(next.size)}; the limit is ${fmtBytes(INBOUND_KNOWLEDGE_MAX_BYTES)}.`);
            if (fileRef.current) fileRef.current.value = "";
            return;
        }
        onChange(next);
    };
    return (
        <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 dark:border-emerald-900 dark:bg-emerald-950/30">
                <BookOpen className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                <div className="text-sm text-gray-700 dark:text-zinc-300">
                    <p className="font-medium text-gray-900 dark:text-zinc-100">This agent&apos;s own knowledge</p>
                    <p className="mt-0.5">
                        A Markdown or text file with everything this inbound agent should know — services, pricing,
                        FAQs, policies. It is parsed into sections the agent answers from. It belongs to this inbound
                        campaign only; outbound campaigns never see it.
                    </p>
                </div>
            </div>
            <input
                ref={fileRef}
                type="file"
                accept=".md,.txt,text/markdown,text/plain"
                className="hidden"
                data-testid="inbound-knowledge-file"
                onChange={(e) => pick(e.target.files?.[0] ?? null)}
            />
            {file ? (
                <div className="flex items-center gap-3 rounded-lg border border-gray-200 px-3 py-2.5 dark:border-white/10">
                    <FileText className="h-5 w-5 text-emerald-500" aria-hidden />
                    <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium text-gray-900 dark:text-zinc-100">{file.name}</div>
                        <div className="text-xs text-muted-foreground">{fmtBytes(file.size)}</div>
                    </div>
                    <button
                        type="button"
                        aria-label="Remove knowledge file"
                        onClick={() => { onChange(null); if (fileRef.current) fileRef.current.value = ""; }}
                        className="rounded p-1 text-muted-foreground hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40"
                    >
                        <X className="h-4 w-4" aria-hidden />
                    </button>
                </div>
            ) : (
                <button
                    type="button"
                    disabled={disabled}
                    onClick={() => fileRef.current?.click()}
                    className="flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-300 px-4 py-10 text-center hover:border-emerald-400 dark:border-white/15"
                >
                    <Upload className="h-7 w-7 text-muted-foreground" aria-hidden />
                    <span className="text-sm font-medium text-gray-900 dark:text-zinc-100">Choose a .md or .txt file</span>
                    <span className="text-xs text-muted-foreground">up to {fmtBytes(INBOUND_KNOWLEDGE_MAX_BYTES)}</span>
                </button>
            )}
            <p className="text-xs text-muted-foreground">
                You can skip this and add or edit knowledge later on this inbound campaign&apos;s page.
            </p>
        </div>
    );
}
