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

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, BookOpen, Check, FileText, Loader2, Upload, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, PersonaType, CampaignCallingSchedule } from "@/lib/dashboard-api";
import { api } from "@/lib/api";
import { AgentNameGender, pruneGenders } from "@/components/campaigns/agent-name-gender";
import { PERSONAS, parseAgentNames } from "@/lib/campaign-personas";
import { VoiceProviderPicker } from "@/components/campaigns/voice-provider-picker";
import { CallingScheduleEditor } from "@/components/campaigns/calling-schedule-editor";

const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const STEPS = ["Basics", "Knowledge", "Review"] as const;

function fmtBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function CampaignWizard() {
    const router = useRouter();

    const [step, setStep] = useState(0);
    const [error, setError] = useState<string | null>(null);

    // Step 1 — basics
    const [name, setName] = useState("");
    const [companyName, setCompanyName] = useState("");
    const [personaType, setPersonaType] = useState<PersonaType>("lead_gen");
    const [agentNamesRaw, setAgentNamesRaw] = useState("");
    const [agentGenders, setAgentGenders] = useState<Record<string, string>>({});
    const [voiceId, setVoiceId] = useState("");
    const [voiceName, setVoiceName] = useState("");
    const [provider, setProvider] = useState("");
    const [goal, setGoal] = useState("");
    const [schedule, setSchedule] = useState<CampaignCallingSchedule>({});

    // Step 2 — knowledge
    const [file, setFile] = useState<File | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    // Step 3 — preview + submit
    const [preview, setPreview] = useState<{ system_prompt: string; greeting: string } | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);

    const agentNames = useMemo(() => parseAgentNames(agentNamesRaw), [agentNamesRaw]);
    const basicsValid = name.trim() && companyName.trim() && agentNames.length >= 1 && voiceId;

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
    };

    const goToReview = async () => {
        setStep(2);
        setPreviewLoading(true);
        setPreview(null);
        try {
            const res = await dashboardApi.previewCampaignPrompt({
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_name: agentNames[0] ?? "Alex",
                campaign_slots: {},
                additional_instructions: goal.trim() || undefined,
                direction: "outbound",
                knowledge_driven: true,
            });
            setPreview({ system_prompt: res.system_prompt, greeting: res.greeting });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Couldn't build a preview");
        } finally {
            setPreviewLoading(false);
        }
    };

    const onCreate = async () => {
        setSubmitting(true);
        setError(null);
        try {
            const { campaign } = await dashboardApi.createCampaign({
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
                knowledge_driven: true,
                calling_schedule: schedule,
            });
            if (file) {
                try {
                    await api.uploadCampaignKnowledge(campaign.id, file);
                } catch {
                    router.push(`/campaigns/${campaign.id}?knowledge_error=1`);
                    return;
                }
            }
            router.push(`/campaigns/${campaign.id}`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to create campaign");
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

            {error && (
                <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
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
                                <Label htmlFor="cw-company">Company name</Label>
                                <Input id="cw-company" value={companyName} onChange={(e) => setCompanyName(e.target.value)} placeholder="Talk-Lee" className="mt-1" />
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
                            <Label htmlFor="cw-agents">Agent names</Label>
                            <Input id="cw-agents" value={agentNamesRaw} onChange={(e) => setAgentNamesRaw(e.target.value)} placeholder="Alex, Jordan, Sam" className="mt-1" />
                            <p className="mt-1 text-xs text-muted-foreground">
                                1–3 names, comma-separated. The agent introduces itself with one (rotated per call).
                                {agentNames.length > 0 && <span className="ml-1 text-emerald-600 dark:text-emerald-400">{agentNames.length} name{agentNames.length > 1 ? "s" : ""}.</span>}
                            </p>
                            <AgentNameGender names={agentNames} value={agentGenders} onChange={setAgentGenders} />
                        </div>

                        <div>
                            <Label htmlFor="cw-goal">Goal <span className="text-muted-foreground font-normal">(optional)</span></Label>
                            <textarea
                                id="cw-goal" value={goal} onChange={(e) => setGoal(e.target.value)} rows={2}
                                placeholder="e.g. Book a free 30-minute discovery call."
                                className="mt-1 w-full rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                            />
                        </div>

                        <VoiceProviderPicker
                            personaType={personaType}
                            voiceId={voiceId}
                            onVoiceChange={(id, nm) => { setVoiceId(id); setVoiceName(nm ?? ""); }}
                            onProviderChange={setProvider}
                        />

                        <div className="rounded-lg border border-border bg-background/50 p-4">
                            <CallingScheduleEditor value={schedule} onChange={setSchedule} />
                        </div>

                        <div className="flex justify-end pt-1">
                            <Button onClick={() => { setError(null); setStep(1); }} disabled={!basicsValid}>
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

                        <div className="flex justify-between pt-1">
                            <Button variant="ghost" onClick={() => setStep(0)}><ArrowLeft className="h-4 w-4" /> Back</Button>
                            <Button onClick={goToReview}>Next: Review <ArrowRight className="h-4 w-4" /></Button>
                        </div>
                    </div>
                )}

                {step === 2 && (
                    <div className="space-y-5">
                        <div className="grid gap-3 sm:grid-cols-2 text-sm">
                            <SummaryRow label="Campaign" value={name} />
                            <SummaryRow label="Company" value={companyName} />
                            <SummaryRow label="Persona" value={PERSONAS.find((p) => p.value === personaType)?.title ?? personaType} />
                            <SummaryRow label="Agents" value={agentNames.join(", ")} />
                            <SummaryRow label="Voice" value={voiceName ? `${voiceName}${provider ? ` (${provider})` : ""}` : voiceId} />
                            <SummaryRow label="Knowledge" value={file ? file.name : "— none —"} />
                        </div>

                        <div>
                            <Label>How the agent will open</Label>
                            <div className="mt-1 rounded-lg border border-gray-200 dark:border-white/10 bg-gray-50 dark:bg-white/5 px-3 py-2 text-sm italic text-gray-700 dark:text-zinc-300 min-h-[2.5rem]">
                                {previewLoading ? <span className="inline-flex items-center gap-2 text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" /> building preview…</span>
                                    : preview?.greeting || "—"}
                            </div>
                        </div>

                        {preview?.system_prompt && (
                            <details className="rounded-lg border border-gray-200 dark:border-white/10">
                                <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-900 dark:text-zinc-100">
                                    Preview the system prompt
                                </summary>
                                <pre className="max-h-64 overflow-auto whitespace-pre-wrap border-t border-gray-200 dark:border-white/10 px-3 py-2 text-xs text-muted-foreground">
                                    {preview.system_prompt}
                                </pre>
                            </details>
                        )}

                        <div className="flex justify-between pt-1">
                            <Button variant="ghost" onClick={() => setStep(1)} disabled={submitting}><ArrowLeft className="h-4 w-4" /> Back</Button>
                            <Button onClick={onCreate} disabled={submitting}>
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
        <div className="flex items-baseline gap-2">
            <span className="w-20 shrink-0 text-xs uppercase tracking-wide text-muted-foreground">{label}</span>
            <span className="truncate font-medium text-gray-900 dark:text-zinc-100">{value || "—"}</span>
        </div>
    );
}

export default CampaignWizard;
