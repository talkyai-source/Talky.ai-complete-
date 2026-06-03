"use client";

/**
 * Campaign creation wizard — the simplified, knowledge-first flow (P4).
 *
 * Three steps:
 *   1. Basics    — name, company, persona, agent names, voice, goal.
 *   2. Knowledge — upload one .md/.txt; this IS the campaign's content. The
 *                  backend parses it into a tree, LLM-enriches it, and the
 *                  agent answers from it on calls (vectorless RAG).
 *   3. Review    — preview the composed prompt + greeting, then create.
 *
 * On submit it creates the campaign with `knowledge_driven: true` (so the
 * backend renders a lean identity+tone prompt and does NOT demand the old
 * per-persona content slots), then uploads the knowledge doc, then routes to
 * the campaign. The classic slot-by-slot form still lives at
 * <CampaignForm> for power users / editing.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
    ArrowLeft, ArrowRight, BookOpen, Check, FileText, Loader2, Play, Sparkles, Square, Upload, X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, PersonaType } from "@/lib/dashboard-api";
import { api } from "@/lib/api";
import { aiOptionsApi, AIProviderConfig, ModelInfo, VoiceInfo } from "@/lib/ai-options-api";
import {
    PERSONAS,
    compareVoicesByPersonaRecommendation,
    isRecommendedVoiceForPersona,
    parseAgentNames,
} from "@/lib/campaign-personas";

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
    const [voiceId, setVoiceId] = useState("");
    const [goal, setGoal] = useState("");

    // Step 2 — knowledge
    const [file, setFile] = useState<File | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    // Voice + TTS provider
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [ttsModels, setTtsModels] = useState<ModelInfo[]>([]);
    const [config, setConfig] = useState<AIProviderConfig | null>(null);
    const [provider, setProvider] = useState<string>("");      // selected provider filter
    const [playingId, setPlayingId] = useState<string | null>(null);
    const audioCtxRef = useRef<AudioContext | null>(null);

    // Step 3 — preview + submit
    const [preview, setPreview] = useState<{ system_prompt: string; greeting: string } | null>(null);
    const [previewLoading, setPreviewLoading] = useState(false);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        let cancelled = false;
        Promise.all([
            aiOptionsApi.getVoices().then((r) => r.voices).catch(() => [] as VoiceInfo[]),
            aiOptionsApi.getConfig().catch(() => null),
            aiOptionsApi.getProviders().then((p) => p.tts.models).catch(() => [] as ModelInfo[]),
        ]).then(([vs, cfg, models]) => {
            if (cancelled) return;
            setVoices(vs);
            setConfig(cfg);
            setTtsModels(models);
            // Default the provider filter to the account's current TTS provider so
            // the voices shown are valid by default; else the first with voices.
            const withVoices = new Set(vs.map((v) => v.provider));
            setProvider(cfg && withVoices.has(cfg.tts_provider) ? cfg.tts_provider : (vs[0]?.provider ?? ""));
        });
        return () => {
            cancelled = true;
            if (audioCtxRef.current) { void audioCtxRef.current.close(); audioCtxRef.current = null; }
        };
    }, []);

    const agentNames = useMemo(() => parseAgentNames(agentNamesRaw), [agentNamesRaw]);

    const providerOptions = useMemo(
        () => Array.from(new Set(voices.map((v) => v.provider))).filter(Boolean).sort(),
        [voices],
    );
    const sortedVoices = useMemo(
        () => voices
            .filter((v) => !provider || v.provider === provider)
            .sort(compareVoicesByPersonaRecommendation(personaType)),
        [voices, provider, personaType],
    );

    const stopPlayback = () => {
        if (audioCtxRef.current) { void audioCtxRef.current.close(); audioCtxRef.current = null; }
        setPlayingId(null);
    };

    const changeProvider = (p: string) => {
        setProvider(p);
        stopPlayback();
        // Drop the voice selection if it doesn't belong to the new provider.
        if (voiceId && !voices.some((v) => v.id === voiceId && v.provider === p)) setVoiceId("");
    };

    const playVoice = async (v: VoiceInfo) => {
        if (playingId === v.id) { stopPlayback(); return; }   // toggle off
        stopPlayback();
        setPlayingId(v.id);
        try {
            const res = await aiOptionsApi.previewVoice({ voice_id: v.id });
            if (!res.audio_base64) throw new Error("No preview audio returned");
            // Backend returns little-endian float32 PCM @ 24kHz, base64-encoded.
            const raw = atob(res.audio_base64);
            const view = new DataView(new ArrayBuffer(raw.length));
            for (let i = 0; i < raw.length; i++) view.setUint8(i, raw.charCodeAt(i));
            const samples = new Float32Array(raw.length / 4);
            for (let i = 0; i < samples.length; i++) samples[i] = view.getFloat32(i * 4, true);
            const ctx = new AudioContext({ sampleRate: 24000 });
            audioCtxRef.current = ctx;
            const buf = ctx.createBuffer(1, samples.length, 24000);
            buf.getChannelData(0).set(samples);
            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(ctx.destination);
            src.onended = () => {
                if (audioCtxRef.current === ctx) { void ctx.close(); audioCtxRef.current = null; }
                setPlayingId((cur) => (cur === v.id ? null : cur));
            };
            src.start();
        } catch (err) {
            stopPlayback();
            setError(err instanceof Error ? err.message : "Couldn't play that voice");
        }
    };

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
            // TTS provider is an account-wide setting here, and the backend
            // validates a campaign's voice against it. If the chosen provider
            // differs from the account's current one, switch it (provider +
            // a valid model + this voice) so the voice validates and calls use
            // the right engine.
            if (config && provider && provider !== config.tts_provider) {
                const model = ttsModels.find((m) => m.provider === provider)?.id ?? config.tts_model;
                await aiOptionsApi.saveConfig({
                    ...config,
                    tts_provider: provider,
                    tts_model: model,
                    tts_voice_id: voiceId,
                });
            }
            const { campaign } = await dashboardApi.createCampaign({
                name: name.trim(),
                description: undefined,
                system_prompt: goal.trim(),      // additional instructions
                voice_id: voiceId,
                goal: goal.trim() || undefined,
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_names: agentNames,
                campaign_slots: {},
                knowledge_driven: true,
            });
            // Chain the knowledge upload (best-effort: the campaign exists even
            // if the doc fails, and the user can re-upload from its page).
            if (file) {
                try {
                    await api.uploadCampaignKnowledge(campaign.id, file);
                } catch {
                    // Campaign exists; surface the upload issue on its page so
                    // the user can retry from the knowledge panel.
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
                        </div>

                        <div>
                            <Label htmlFor="cw-goal">Goal <span className="text-muted-foreground font-normal">(optional)</span></Label>
                            <textarea
                                id="cw-goal" value={goal} onChange={(e) => setGoal(e.target.value)} rows={2}
                                placeholder="e.g. Book a free 30-minute discovery call."
                                className="mt-1 w-full rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                            />
                        </div>

                        <div>
                            <div className="flex items-center justify-between gap-3">
                                <Label>Voice</Label>
                                {providerOptions.length > 0 && (
                                    <div className="flex items-center gap-1.5">
                                        <span className="text-xs text-muted-foreground">Provider</span>
                                        <select
                                            value={provider}
                                            onChange={(e) => changeProvider(e.target.value)}
                                            className="rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-2 py-1 text-xs capitalize focus:outline-none focus:ring-2 focus:ring-emerald-500"
                                        >
                                            {providerOptions.map((p) => (
                                                <option key={p} value={p}>{p}</option>
                                            ))}
                                        </select>
                                    </div>
                                )}
                            </div>
                            {config && provider && config.tts_provider !== provider && (
                                <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">
                                    Switching from “{config.tts_provider}” changes the TTS provider for your whole account.
                                </p>
                            )}
                            {sortedVoices.length === 0 ? (
                                <p className="mt-1 text-xs text-muted-foreground">
                                    {voices.length === 0 ? "Loading voices…" : "No voices for this provider."}
                                </p>
                            ) : (
                                <div className="mt-1 grid max-h-56 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
                                    {sortedVoices.map((v) => {
                                        const rec = isRecommendedVoiceForPersona(v, personaType);
                                        const selected = voiceId === v.id;
                                        const playing = playingId === v.id;
                                        return (
                                            <div
                                                key={v.id}
                                                className={`flex items-center gap-1 rounded-lg border pr-1 text-sm transition ${
                                                    selected
                                                        ? "border-emerald-500 ring-1 ring-emerald-500 bg-emerald-50 dark:bg-emerald-950/40"
                                                        : "border-gray-200 dark:border-white/10 hover:border-gray-300"}`}
                                            >
                                                <button
                                                    type="button"
                                                    onClick={() => setVoiceId(v.id)}
                                                    className="flex min-w-0 flex-1 items-center justify-between gap-2 px-3 py-2 text-left"
                                                >
                                                    <span className="min-w-0">
                                                        <span className="block truncate font-medium text-gray-900 dark:text-zinc-100">{v.name}</span>
                                                        <span className="block truncate text-xs text-muted-foreground">
                                                            {[v.gender, v.accent].filter(Boolean).join(" · ") || v.language}
                                                        </span>
                                                    </span>
                                                    {rec && (
                                                        <span className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                                                            <Sparkles className="h-2.5 w-2.5" /> Rec
                                                        </span>
                                                    )}
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => playVoice(v)}
                                                    title={playing ? "Stop" : "Play sample"}
                                                    aria-label={playing ? "Stop preview" : "Play voice preview"}
                                                    className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/40"
                                                >
                                                    {playing ? <Square className="h-3.5 w-3.5 fill-current" /> : <Play className="h-3.5 w-3.5" />}
                                                </button>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
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
                            <SummaryRow label="Voice" value={voices.find((v) => v.id === voiceId)?.name ?? voiceId} />
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
