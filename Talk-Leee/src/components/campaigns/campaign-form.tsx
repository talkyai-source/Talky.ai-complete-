"use client";

/*
 * Shared campaign-form component used by both:
 *   - /campaigns/new        (mode="create")
 *   - /campaigns/[id]/edit  (mode="edit", with initialData prefill)
 *
 * The two routes render the EXACT same UI — voice cards, persona radios,
 * persona-specific slot fields, additional-instructions textarea — so a
 * user editing a campaign sees the same screen they used to create it,
 * with every field pre-populated from the saved record.
 *
 * Field ↔ backend mapping is one-to-one with `CampaignCreateRequest` /
 * `CampaignUpdateRequest` on the backend; the submit handler dispatches
 * to `dashboardApi.createCampaign` or `dashboardApi.updateCampaign` based
 * on `mode`.
 */

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, PersonaType } from "@/lib/dashboard-api";
import {
    PERSONAS,
    PersonaSpec,
    SlotDef,
    isRecommendedVoiceForPersona,
    parseAgentNames,
    parseKvList,
    parseList,
} from "@/lib/campaign-personas";
import { aiOptionsApi, AIProviderConfig, VoiceInfo } from "@/lib/ai-options-api";
import { captureException } from "@/lib/monitoring";
import { ChevronDown, Loader2, Play, RefreshCw, Square, Volume2, Check } from "lucide-react";
import { motion } from "framer-motion";

export type CampaignFormMode = "create" | "edit";

export interface CampaignFormInitial {
    name: string;
    description: string;
    system_prompt: string;
    voice_id: string;
    goal: string;
    persona_type: PersonaType;
    company_name: string;
    agent_names: string[];
    /** Optional per-name gender ("male"|"female") to match the voice. */
    agent_name_genders?: Record<string, string>;
    /** Persona-specific slot values — keyed by slot.key. */
    slots: Record<string, string>;
}

interface Props {
    mode: CampaignFormMode;
    /** Required when `mode === "edit"`; ignored otherwise. */
    campaignId?: string;
    /** Pre-fill values; defaults to an empty form when omitted. */
    initialData?: CampaignFormInitial;
}

const EMPTY_INITIAL: CampaignFormInitial = {
    name: "",
    description: "",
    system_prompt: "",
    voice_id: "",
    goal: "",
    persona_type: "lead_gen",
    company_name: "",
    agent_names: [],
    slots: {},
};

export function CampaignForm({ mode, campaignId, initialData }: Props) {
    const router = useRouter();
    const isEdit = mode === "edit";
    const seed = initialData ?? EMPTY_INITIAL;

    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState("");
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [loadingVoices, setLoadingVoices] = useState(true);
    const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);
    const [globalAiConfig, setGlobalAiConfig] = useState<AIProviderConfig | null>(null);
    const previewAudioRef = useRef<HTMLAudioElement | null>(null);
    // Track the Web Audio context too, so the Stop button can interrupt
    // a base64-PCM preview mid-playback (HTMLAudio path uses .pause(),
    // Web Audio path needs context.close()).
    const previewCtxRef = useRef<AudioContext | null>(null);

    // Player-style dropdown state.
    const [voicePickerOpen, setVoicePickerOpen] = useState(false);
    const voicePickerRef = useRef<HTMLDivElement | null>(null);

    // Close the dropdown when the user clicks outside of it.
    useEffect(() => {
        if (!voicePickerOpen) return;
        function onDocClick(e: MouseEvent) {
            const el = voicePickerRef.current;
            if (el && !el.contains(e.target as Node)) {
                setVoicePickerOpen(false);
            }
        }
        document.addEventListener("mousedown", onDocClick);
        return () => document.removeEventListener("mousedown", onDocClick);
    }, [voicePickerOpen]);

    const [formData, setFormData] = useState({
        name: seed.name,
        description: seed.description,
        system_prompt: seed.system_prompt,
        voice_id: seed.voice_id,
        goal: seed.goal,
    });

    const [personaType, setPersonaType] = useState<PersonaType>(seed.persona_type);
    const [companyName, setCompanyName] = useState<string>(seed.company_name);
    const [agentNamesRaw, setAgentNamesRaw] = useState<string>(seed.agent_names.join(", "));
    const [slotValues, setSlotValues] = useState<Record<string, string>>({ ...seed.slots });

    // Prompt preview (T4-B4) — backend renders the assembled system
    // prompt + spoken greeting from the current draft. Open the panel
    // to see exactly what the AI will be told before starting a call.
    const [previewOpen, setPreviewOpen] = useState(false);
    const [previewDirection, setPreviewDirection] = useState<"outbound" | "inbound">("outbound");
    const [previewLoading, setPreviewLoading] = useState(false);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [previewData, setPreviewData] = useState<{
        system_prompt: string;
        greeting: string;
        direction: "outbound" | "inbound";
        has_inbound_directive: boolean;
        prompt_chars: number;
    } | null>(null);

    const persona: PersonaSpec = PERSONAS.find((p) => p.value === personaType) ?? PERSONAS[0];

    function setSlot(key: string, value: string) {
        setSlotValues((prev) => ({ ...prev, [key]: value }));
    }

    // Reset slots only when the persona changes IN edit/create *after* initial mount.
    // We must not wipe prefilled slots on first render of the edit page.
    function changePersona(next: PersonaType) {
        if (next === personaType) return;
        setPersonaType(next);
        setSlotValues({});
    }

    function buildCampaignSlots(): Record<string, unknown> {
        const out: Record<string, unknown> = {};
        for (const slot of persona.slots) {
            const raw = (slotValues[slot.key] ?? "").trim();
            if (!raw) continue;
            if (slot.kind === "list") {
                out[slot.key] = parseList(raw);
            } else if (slot.kind === "kv-list") {
                out[slot.key] = parseKvList(raw);
            } else {
                out[slot.key] = raw;
            }
        }
        return out;
    }

    function missingRequiredSlots(): SlotDef[] {
        return persona.slots.filter((slot) => {
            if (!slot.required) return false;
            const raw = (slotValues[slot.key] ?? "").trim();
            return !raw;
        });
    }

    async function runPreview() {
        // Render-time preview — uses the current draft, never writes.
        // First agent name from the pool is used for the rendered preview;
        // real campaigns rotate, but a preview just needs a concrete name.
        const firstAgent = parseAgentNames(agentNamesRaw)[0] ?? "Alex";
        if (!companyName.trim()) {
            setPreviewError("Add a company name before previewing.");
            return;
        }
        setPreviewLoading(true);
        setPreviewError(null);
        try {
            const result = await dashboardApi.previewCampaignPrompt({
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_name: firstAgent,
                campaign_slots: buildCampaignSlots(),
                additional_instructions: formData.system_prompt || undefined,
                direction: previewDirection,
            });
            setPreviewData(result);
        } catch (err) {
            const msg = err instanceof Error ? err.message : "Preview failed";
            setPreviewError(msg);
            setPreviewData(null);
        } finally {
            setPreviewLoading(false);
        }
    }

    // Fetch voices + global AI config once.
    useEffect(() => {
        async function fetchVoices() {
            try {
                setLoadingVoices(true);
                const [voicesResult, config] = await Promise.all([
                    aiOptionsApi.getVoices(),
                    aiOptionsApi.getConfig(),
                ]);
                const voiceList = voicesResult.voices;
                const providerVoices = voiceList.filter(
                    (voice) => voice.provider === config.tts_provider,
                );
                setVoices(voiceList);
                setGlobalAiConfig(config);
                if (providerVoices.length > 0) {
                    setFormData((prev) =>
                        prev.voice_id && providerVoices.some((v) => v.id === prev.voice_id)
                            ? prev
                            : { ...prev, voice_id: providerVoices[0].id },
                    );
                }
            } catch (err) {
                captureException(err, { area: "campaign-form", kind: "voices" });
                setError("Failed to load voices.");
            } finally {
                setLoadingVoices(false);
            }
        }
        void fetchVoices();
    }, []);

    function handleChange(
        e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>,
    ) {
        setFormData((prev) => ({ ...prev, [e.target.name]: e.target.value }));
    }

    function stopPreview() {
        if (previewAudioRef.current) {
            try {
                previewAudioRef.current.pause();
                previewAudioRef.current.currentTime = 0;
            } catch { /* ignore */ }
            previewAudioRef.current = null;
        }
        if (previewCtxRef.current) {
            try {
                void previewCtxRef.current.close();
            } catch { /* ignore */ }
            previewCtxRef.current = null;
        }
        setPreviewingVoiceId(null);
    }

    async function handlePreviewVoice(voiceId: string) {
        const voice = voices.find((item) => item.id === voiceId);
        try {
            // Stop anything currently playing before kicking off the new preview
            // so two voices never overlap.
            stopPreview();
            setPreviewingVoiceId(voiceId);

            if (voice?.preview_url) {
                const audio = new Audio(voice.preview_url);
                previewAudioRef.current = audio;
                audio.onended = () => {
                    if (previewAudioRef.current === audio) previewAudioRef.current = null;
                    setPreviewingVoiceId(null);
                };
                audio.onerror = () => {
                    if (previewAudioRef.current === audio) previewAudioRef.current = null;
                    setPreviewingVoiceId(null);
                };
                await audio.play();
                return;
            }

            const response = await aiOptionsApi.previewVoice({
                voice_id: voiceId,
                text: "Hello, this is your phone representative. How can I help you today?",
            });

            const audioData = atob(response.audio_base64);
            const audioArray = new Float32Array(audioData.length / 4);
            const dataView = new DataView(new ArrayBuffer(audioData.length));
            for (let i = 0; i < audioData.length; i++) {
                dataView.setUint8(i, audioData.charCodeAt(i));
            }
            for (let i = 0; i < audioArray.length; i++) {
                audioArray[i] = dataView.getFloat32(i * 4, true);
            }

            const sampleRate =
                voice?.provider === "cartesia" ||
                voice?.provider === "google" ||
                voice?.provider === "deepgram" ||
                voice?.provider === "elevenlabs"
                    ? 24000
                    : 16000;

            const audioContext = new AudioContext({ sampleRate });
            previewCtxRef.current = audioContext;
            const audioBuffer = audioContext.createBuffer(1, audioArray.length, sampleRate);
            audioBuffer.getChannelData(0).set(audioArray);

            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            source.start();

            source.onended = () => {
                if (previewCtxRef.current === audioContext) {
                    void audioContext.close();
                    previewCtxRef.current = null;
                }
                setPreviewingVoiceId(null);
            };
        } catch (err) {
            captureException(err, { area: "campaign-form", kind: "voice-preview" });
            setPreviewingVoiceId(null);
        }
    }

    useEffect(() => {
        return () => {
            stopPreview();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const campaignVoices = globalAiConfig
        ? voices.filter((voice) => voice.provider === globalAiConfig.tts_provider)
        : voices;

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!formData.voice_id) {
            setError("Select a voice from the active global TTS provider before saving the campaign.");
            return;
        }
        const agentNames = parseAgentNames(agentNamesRaw);
        if (agentNames.length === 0) {
            setError("Add at least one agent name (up to three — we rotate per call).");
            return;
        }
        if (!companyName.trim()) {
            setError("Add the company or business name your agent represents.");
            return;
        }
        const missing = missingRequiredSlots();
        if (missing.length > 0) {
            setError(`Missing required fields: ${missing.map((s) => s.label).join(", ")}`);
            return;
        }

        setSubmitting(true);
        setError("");

        const payload = {
            name: formData.name,
            description: formData.description || undefined,
            system_prompt: formData.system_prompt,
            voice_id: formData.voice_id,
            goal: formData.goal || undefined,
            persona_type: personaType,
            company_name: companyName.trim(),
            agent_names: agentNames,
            campaign_slots: buildCampaignSlots(),
        };

        try {
            if (isEdit) {
                if (!campaignId) {
                    throw new Error("Internal error: edit mode without a campaignId.");
                }
                const result = await dashboardApi.updateCampaign(campaignId, payload);
                router.push(`/campaigns/${result.campaign.id}`);
            } else {
                const result = await dashboardApi.createCampaign(payload);
                router.push(`/campaigns/${result.campaign.id}`);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to save campaign");
        } finally {
            setSubmitting(false);
        }
    }

    const submitLabel = isEdit ? "Save Campaign" : "Create Campaign";
    const submittingLabel = isEdit ? "Saving..." : "Creating...";

    return (
        <div className="max-w-2xl">
            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="content-card"
            >
                <h3 className="text-lg font-semibold text-foreground mb-6">Campaign Details</h3>
                <form onSubmit={handleSubmit} className="space-y-6">
                    {/* Name */}
                    <div className="space-y-2">
                        <Label htmlFor="name">Campaign Name</Label>
                        <Input
                            id="name"
                            name="name"
                            placeholder="e.g., Q1 Sales Outreach"
                            value={formData.name}
                            onChange={handleChange}
                            required
                            disabled={submitting}
                        />
                    </div>

                    {/* Description */}
                    <div className="space-y-2">
                        <Label htmlFor="description">Description (optional)</Label>
                        <textarea
                            id="description"
                            name="description"
                            placeholder="Brief description of this campaign..."
                            value={formData.description}
                            onChange={handleChange}
                            disabled={submitting}
                            rows={2}
                            className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                        />
                    </div>

                    {/* Goal */}
                    <div className="space-y-2">
                        <Label htmlFor="goal">Campaign Goal (optional)</Label>
                        <textarea
                            id="goal"
                            name="goal"
                            placeholder="e.g., Schedule a demo, collect feedback, qualify the lead and capture their timeline…"
                            value={formData.goal}
                            onChange={handleChange}
                            disabled={submitting}
                            rows={4}
                            maxLength={4000}
                            className="flex w-full min-h-[110px] resize-y rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                        />
                        <div className="flex items-center justify-between">
                            <p className="text-xs text-muted-foreground">
                                Define what success looks like for each call
                            </p>
                            <span className="text-xs text-muted-foreground">
                                {(formData.goal?.length ?? 0).toLocaleString()}/4,000
                            </span>
                        </div>
                    </div>

                    {/* Voice picker */}
                    <div className="space-y-2">
                        <Label>AI Voice ({campaignVoices.length} available)</Label>
                        {globalAiConfig && (
                            <p className="text-xs text-muted-foreground">
                                Campaign voices follow your global AI Options TTS provider:{" "}
                                <span className="font-medium text-foreground">{globalAiConfig.tts_provider}</span>{" "}
                                on model{" "}
                                <span className="font-medium text-foreground">{globalAiConfig.tts_model}</span>.
                            </p>
                        )}
                        {/*
                         * Voice player widget — replaces the plain native <select>.
                         * Closed state: shows the currently-selected voice as a
                         *   single-row "now-playing" card with a Play / Stop
                         *   button (so the user can audition the chosen voice
                         *   without scrolling the card grid).
                         * Open state: drops a panel listing every voice across
                         *   every provider, grouped by provider. Picking a
                         *   voice closes the panel, sets `voice_id`, AND
                         *   immediately plays its preview.
                         */}
                        {!loadingVoices && voices.length > 0 && (() => {
                            const selectedVoice =
                                voices.find((v) => v.id === formData.voice_id) ?? null;
                            const isPlayingSelected =
                                !!selectedVoice && previewingVoiceId === selectedVoice.id;
                            const grouped = Object.entries(
                                voices.reduce<Record<string, VoiceInfo[]>>((acc, v) => {
                                    const key = v.provider || "other";
                                    (acc[key] ||= []).push(v);
                                    return acc;
                                }, {}),
                            ).sort(([a], [b]) => a.localeCompare(b));

                            return (
                                <div className="relative space-y-2" ref={voicePickerRef}>
                                    {/* Trigger row — player UI for the selected voice */}
                                    <div
                                        className={`flex w-full items-center gap-3 rounded-lg border bg-background px-3 py-2 transition-colors ${
                                            voicePickerOpen
                                                ? "border-emerald-500"
                                                : "border-input hover:border-input"
                                        }`}
                                    >
                                        {/* Stop / Play for the selected voice */}
                                        <button
                                            type="button"
                                            disabled={submitting || !selectedVoice}
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                if (!selectedVoice) return;
                                                if (isPlayingSelected) {
                                                    stopPreview();
                                                } else {
                                                    void handlePreviewVoice(selectedVoice.id);
                                                }
                                            }}
                                            aria-label={isPlayingSelected ? "Stop preview" : "Play voice preview"}
                                            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-all hover:scale-105 disabled:cursor-not-allowed disabled:opacity-50"
                                            style={{
                                                backgroundColor:
                                                    (selectedVoice?.accent_color || "#10B981") + "30",
                                            }}
                                        >
                                            {isPlayingSelected ? (
                                                <Square
                                                    className="h-4 w-4"
                                                    style={{ color: selectedVoice?.accent_color || "#10B981" }}
                                                />
                                            ) : (
                                                <Play
                                                    className="h-4 w-4"
                                                    style={{ color: selectedVoice?.accent_color || "#10B981" }}
                                                />
                                            )}
                                        </button>

                                        {/* Selected-voice info / "Choose a voice" empty state */}
                                        <button
                                            type="button"
                                            disabled={submitting}
                                            onClick={() => setVoicePickerOpen((o) => !o)}
                                            className="flex flex-1 items-center justify-between gap-2 text-left disabled:cursor-not-allowed disabled:opacity-50"
                                        >
                                            <div className="min-w-0">
                                                {selectedVoice ? (
                                                    <>
                                                        <div className="flex items-center gap-2">
                                                            <Volume2
                                                                className="h-4 w-4 shrink-0"
                                                                style={{
                                                                    color: selectedVoice.accent_color || "#10B981",
                                                                }}
                                                            />
                                                            <span className="truncate text-sm font-medium text-foreground">
                                                                {selectedVoice.name}
                                                            </span>
                                                        </div>
                                                        <div className="mt-1 flex flex-wrap items-center gap-1">
                                                            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                                                                {selectedVoice.provider}
                                                            </span>
                                                            {selectedVoice.gender && (
                                                                <span
                                                                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                                                        selectedVoice.gender === "female"
                                                                            ? "bg-pink-500/20 text-pink-400"
                                                                            : "bg-blue-500/20 text-blue-400"
                                                                    }`}
                                                                >
                                                                    {selectedVoice.gender}
                                                                </span>
                                                            )}
                                                            {selectedVoice.language && (
                                                                <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                                                                    {selectedVoice.language.toUpperCase()}
                                                                </span>
                                                            )}
                                                        </div>
                                                    </>
                                                ) : (
                                                    <span className="text-sm text-muted-foreground">
                                                        Choose a voice…
                                                    </span>
                                                )}
                                            </div>
                                            <ChevronDown
                                                className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${
                                                    voicePickerOpen ? "rotate-180" : ""
                                                }`}
                                            />
                                        </button>
                                    </div>

                                    {/* Cross-provider safety warning (still shown when collapsed) */}
                                    {selectedVoice &&
                                        globalAiConfig &&
                                        selectedVoice.provider !== globalAiConfig.tts_provider && (
                                            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200">
                                                Heads up: this voice belongs to{" "}
                                                <span className="font-semibold">{selectedVoice.provider}</span>, but
                                                your global TTS provider is{" "}
                                                <span className="font-semibold">
                                                    {globalAiConfig.tts_provider}
                                                </span>
                                                . Switch the global provider in AI Options before launching this
                                                campaign, otherwise calls will fail at synthesis time.
                                            </div>
                                        )}

                                    {/* Expanded picker panel */}
                                    {voicePickerOpen && (
                                        <div className="absolute left-0 right-0 top-full z-20 mt-2 max-h-80 overflow-auto rounded-lg border border-border bg-background p-2 shadow-lg">
                                            {grouped.map(([provider, list]) => (
                                                <div key={provider} className="mb-2 last:mb-0">
                                                    <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                                                        {provider}
                                                        {globalAiConfig?.tts_provider === provider
                                                            ? " · active"
                                                            : ""}
                                                    </div>
                                                    {list
                                                        .slice()
                                                        .sort((a, b) => {
                                                            // Recommended-for-persona voices float to the top of
                                                            // their provider group; ties break alphabetically.
                                                            // Operators can still pick anything — the sort just
                                                            // surfaces the on-tone choices first.
                                                            const ra = isRecommendedVoiceForPersona(a, personaType) ? 1 : 0;
                                                            const rb = isRecommendedVoiceForPersona(b, personaType) ? 1 : 0;
                                                            if (ra !== rb) return rb - ra;
                                                            return (a.name || "").localeCompare(b.name || "");
                                                        })
                                                        .map((voice) => {
                                                            const isSelected = formData.voice_id === voice.id;
                                                            const isRecommended = isRecommendedVoiceForPersona(voice, personaType);
                                                            return (
                                                                <button
                                                                    type="button"
                                                                    key={voice.id}
                                                                    onClick={() => {
                                                                        setFormData((prev) => ({
                                                                            ...prev,
                                                                            voice_id: voice.id,
                                                                        }));
                                                                        setVoicePickerOpen(false);
                                                                        // Auto-play the chosen voice so the
                                                                        // user immediately hears their pick.
                                                                        void handlePreviewVoice(voice.id);
                                                                    }}
                                                                    className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
                                                                        isSelected
                                                                            ? "bg-emerald-500/15 text-foreground"
                                                                            : "text-foreground hover:bg-muted"
                                                                    }`}
                                                                >
                                                                    <Volume2
                                                                        className="h-3.5 w-3.5 shrink-0"
                                                                        style={{
                                                                            color: voice.accent_color || "#10B981",
                                                                        }}
                                                                    />
                                                                    <span className="flex-1 truncate">
                                                                        {voice.name}
                                                                    </span>
                                                                    {isRecommended && (
                                                                        <span
                                                                            className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300"
                                                                            title={`Recommended for ${personaType.replace("_", " ")}`}
                                                                        >
                                                                            recommended
                                                                        </span>
                                                                    )}
                                                                    {voice.gender && (
                                                                        <span
                                                                            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                                                                voice.gender === "female"
                                                                                    ? "bg-pink-500/20 text-pink-400"
                                                                                    : "bg-blue-500/20 text-blue-400"
                                                                            }`}
                                                                        >
                                                                            {voice.gender}
                                                                        </span>
                                                                    )}
                                                                    {voice.language && (
                                                                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                                                                            {voice.language.toUpperCase()}
                                                                        </span>
                                                                    )}
                                                                    {isSelected && (
                                                                        <Check className="h-4 w-4 text-emerald-400" />
                                                                    )}
                                                                </button>
                                                            );
                                                        })}
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            );
                        })()}

                        {loadingVoices ? (
                            <div className="flex items-center justify-center py-8">
                                <RefreshCw className="w-5 h-5 animate-spin text-muted-foreground" />
                                <span className="ml-2 text-muted-foreground">Loading voices...</span>
                            </div>
                        ) : campaignVoices.length === 0 ? (
                            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
                                No voices are available for the current global TTS provider yet. Save a valid provider in AI Options first.
                            </div>
                        ) : (
                            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                {campaignVoices.map((voice) => (
                                    <div
                                        key={voice.id}
                                        onClick={() => setFormData((prev) => ({ ...prev, voice_id: voice.id }))}
                                        className={`relative p-3 rounded-lg border cursor-pointer transition-colors ${
                                            formData.voice_id === voice.id
                                                ? "border-emerald-500 bg-emerald-500/20"
                                                : "border-border bg-muted/30 hover:bg-muted/40"
                                        }`}
                                    >
                                        <button
                                            type="button"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                handlePreviewVoice(voice.id);
                                            }}
                                            disabled={previewingVoiceId === voice.id}
                                            className="absolute top-2 right-2 w-8 h-8 rounded-full flex items-center justify-center transition-all hover:scale-110"
                                            style={{ backgroundColor: (voice.accent_color || "#10B981") + "30" }}
                                        >
                                            {previewingVoiceId === voice.id ? (
                                                <RefreshCw
                                                    className="w-4 h-4 animate-spin"
                                                    style={{ color: voice.accent_color || "#10B981" }}
                                                />
                                            ) : (
                                                <Play
                                                    className="w-4 h-4"
                                                    style={{ color: voice.accent_color || "#10B981" }}
                                                />
                                            )}
                                        </button>

                                        <div className="pr-10">
                                            <div className="flex items-center gap-2">
                                                <div
                                                    className="w-6 h-6 rounded-full flex items-center justify-center"
                                                    style={{ backgroundColor: (voice.accent_color || "#10B981") + "30" }}
                                                >
                                                    <Volume2 className="w-3 h-3" style={{ color: voice.accent_color || "#10B981" }} />
                                                </div>
                                                <p className="font-medium text-sm text-foreground">{voice.name}</p>
                                            </div>
                                            <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                                                {voice.description}
                                            </p>

                                            <div className="mt-2 flex gap-1">
                                                {voice.gender && (
                                                    <span
                                                        className={`text-xs px-1.5 py-0.5 rounded ${
                                                            voice.gender === "female"
                                                                ? "bg-pink-500/20 text-pink-400"
                                                                : "bg-blue-500/20 text-blue-400"
                                                        }`}
                                                    >
                                                        {voice.gender}
                                                    </span>
                                                )}
                                                <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                                                    {(voice.language || "unknown").toUpperCase()}
                                                </span>
                                            </div>
                                        </div>

                                        {formData.voice_id === voice.id && (
                                            <div className="absolute bottom-2 right-2">
                                                <Check className="w-4 h-4 text-emerald-400" />
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Persona picker */}
                    <div className="space-y-2">
                        <Label>Agent role</Label>
                        <p className="text-xs text-muted-foreground">
                            Each role shares our generic voice guardrails (pacing, phrasing, interruption handling). You only fill in the business-specific details.
                        </p>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                            {PERSONAS.map((p) => (
                                <label
                                    key={p.value}
                                    className={`flex cursor-pointer flex-col gap-1 rounded-lg border p-3 transition-colors ${
                                        personaType === p.value
                                            ? "border-emerald-500 bg-emerald-500/10"
                                            : "border-border bg-muted/30 hover:bg-muted/40"
                                    }`}
                                >
                                    <div className="flex items-center gap-2">
                                        <input
                                            type="radio"
                                            name="persona_type"
                                            value={p.value}
                                            checked={personaType === p.value}
                                            onChange={() => changePersona(p.value)}
                                            disabled={submitting}
                                            className="mt-0"
                                        />
                                        <span className="text-sm font-medium text-foreground">{p.title}</span>
                                    </div>
                                    <span className="text-xs text-muted-foreground">{p.summary}</span>
                                </label>
                            ))}
                        </div>
                    </div>

                    {/*
                     * Prompt preview (T4-B4). Renders the exact system prompt
                     * the LLM will receive plus the spoken greeting. Read-only —
                     * never writes. Lets the operator catch slot typos and
                     * direction mismatches before burning a real call.
                     */}
                    <div className="rounded-lg border border-border bg-muted/20 p-3 space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                                <Label>Prompt preview</Label>
                                <p className="text-xs text-muted-foreground">
                                    See the exact system prompt the AI will be told and the first line it will speak.
                                </p>
                            </div>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => setPreviewOpen((o) => !o)}
                                disabled={submitting}
                            >
                                {previewOpen ? "Hide preview" : "Show preview"}
                            </Button>
                        </div>
                        {previewOpen && (
                            <div className="space-y-3 pt-1">
                                <div className="flex flex-wrap items-center gap-3">
                                    <Label className="text-xs">Direction:</Label>
                                    <select
                                        value={previewDirection}
                                        onChange={(e) =>
                                            setPreviewDirection(e.target.value as "outbound" | "inbound")
                                        }
                                        className="rounded border border-border bg-background px-2 py-1 text-xs"
                                        disabled={previewLoading}
                                    >
                                        <option value="outbound">Outbound (we call them)</option>
                                        <option value="inbound">Inbound (they call us)</option>
                                    </select>
                                    <Button
                                        type="button"
                                        size="sm"
                                        onClick={runPreview}
                                        disabled={previewLoading || !companyName.trim()}
                                    >
                                        {previewLoading ? (
                                            <>
                                                <Loader2 className="h-3 w-3 animate-spin" />
                                                Rendering…
                                            </>
                                        ) : (
                                            "Render preview"
                                        )}
                                    </Button>
                                </div>
                                {previewError && (
                                    <div className="rounded border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
                                        {previewError}
                                    </div>
                                )}
                                {previewData && (
                                    <div className="space-y-3">
                                        <div className="space-y-1">
                                            <Label className="text-xs">
                                                First spoken line ({previewData.direction})
                                            </Label>
                                            <pre className="whitespace-pre-wrap rounded border border-border bg-background p-2 text-xs">
                                                {previewData.greeting}
                                            </pre>
                                        </div>
                                        <div className="space-y-1">
                                            <Label className="text-xs">
                                                System prompt ({previewData.prompt_chars.toLocaleString()} chars
                                                {previewData.has_inbound_directive ? ", inbound directive applied" : ""})
                                            </Label>
                                            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border bg-background p-2 text-[11px] leading-snug">
                                                {previewData.system_prompt}
                                            </pre>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Company name + agent-name pool */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label htmlFor="company_name">Company / business name</Label>
                            <Input
                                id="company_name"
                                name="company_name"
                                placeholder="The name your agent says on the call"
                                value={companyName}
                                onChange={(e) => setCompanyName(e.target.value)}
                                required
                                disabled={submitting}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="agent_names">Agent names (up to 3)</Label>
                            <Input
                                id="agent_names"
                                name="agent_names"
                                placeholder="Alex, Sam, Jordan"
                                value={agentNamesRaw}
                                onChange={(e) => setAgentNamesRaw(e.target.value)}
                                required
                                disabled={submitting}
                            />
                            <p className="text-xs text-muted-foreground">
                                Comma-separated. One is picked per call — supply 2–3 to rotate.
                            </p>
                            {/*
                             * Voice-gender attention notice — surfaces ONLY when the
                             * currently-selected voice's catalog entry carries an
                             * unambiguous male/female gender. The system does not
                             * filter or rewrite the names you type; it just nudges
                             * you to keep the names consistent with the voice you
                             * picked, otherwise the agent might say "I'm Bob" in a
                             * woman's voice.
                             */}
                            {(() => {
                                const selected = voices.find((v) => v.id === formData.voice_id);
                                const gender = (selected?.gender || "").trim().toLowerCase();
                                if (gender !== "male" && gender !== "female") return null;
                                return (
                                    <div
                                        role="note"
                                        className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
                                    >
                                        <span className="font-semibold">Attention: </span>
                                        Please add the names according to the voice — you picked a{" "}
                                        <span className="font-semibold">{gender}</span> voice, so input{" "}
                                        <span className="font-semibold">{gender}</span> names only.
                                    </div>
                                );
                            })()}
                        </div>
                    </div>

                    {/* Persona-specific slot fields */}
                    <div className="space-y-4">
                        <div className="text-sm font-medium text-foreground">
                            {persona.title} — details
                        </div>
                        {persona.slots.map((slot) => (
                            <div key={slot.key} className="space-y-2">
                                <Label htmlFor={`slot-${slot.key}`}>
                                    {slot.label}
                                    {slot.required ? <span className="text-red-400"> *</span> : null}
                                </Label>
                                {slot.kind === "textarea" || slot.kind === "list" || slot.kind === "kv-list" ? (
                                    <textarea
                                        id={`slot-${slot.key}`}
                                        placeholder={slot.placeholder}
                                        value={slotValues[slot.key] ?? ""}
                                        onChange={(e) => setSlot(slot.key, e.target.value)}
                                        disabled={submitting}
                                        rows={slot.kind === "textarea" ? 3 : 4}
                                        className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                                    />
                                ) : (
                                    <Input
                                        id={`slot-${slot.key}`}
                                        placeholder={slot.placeholder}
                                        value={slotValues[slot.key] ?? ""}
                                        onChange={(e) => setSlot(slot.key, e.target.value)}
                                        disabled={submitting}
                                    />
                                )}
                                {slot.help ? <p className="text-xs text-muted-foreground">{slot.help}</p> : null}
                            </div>
                        ))}
                    </div>

                    {/* Additional instructions */}
                    <div className="space-y-2">
                        <Label htmlFor="system_prompt">Additional instructions</Label>
                        <textarea
                            id="system_prompt"
                            name="system_prompt"
                            placeholder="Anything specific to this campaign that the generic rules and persona don't cover."
                            value={formData.system_prompt}
                            onChange={handleChange}
                            disabled={submitting}
                            rows={4}
                            className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                        />
                        <p className="text-xs text-muted-foreground">
                            Layered on top of the generic guardrails and the persona you picked. Optional but recommended for campaign-specific callouts.
                        </p>
                    </div>

                    {error && (
                        <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
                            {error}
                        </div>
                    )}

                    <div className="flex gap-4">
                        <Button type="submit" disabled={submitting || !formData.voice_id}>
                            {submitting ? (
                                <>
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                    {submittingLabel}
                                </>
                            ) : (
                                submitLabel
                            )}
                        </Button>
                        <Button type="button" variant="outline" onClick={() => router.back()} disabled={submitting}>
                            Cancel
                        </Button>
                    </div>
                </form>
            </motion.div>
        </div>
    );
}
