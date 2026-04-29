"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { dashboardApi, PersonaType } from "@/lib/dashboard-api";
import {
    PERSONAS,
    PersonaSpec,
    SlotDef,
    parseAgentNames,
    parseKvList,
    parseList,
} from "@/lib/campaign-personas";
import { aiOptionsApi, AIProviderConfig, VoiceInfo } from "@/lib/ai-options-api";
import { captureException } from "@/lib/monitoring";
import { ArrowLeft, Loader2, Play, RefreshCw, Volume2, Check } from "lucide-react";
import { motion } from "framer-motion";

export default function NewCampaignPage() {
    const router = useRouter();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [loadingVoices, setLoadingVoices] = useState(true);
    const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);
    const [globalAiConfig, setGlobalAiConfig] = useState<AIProviderConfig | null>(null);
    const previewAudioRef = useRef<HTMLAudioElement | null>(null);

    const [formData, setFormData] = useState({
        name: "",
        description: "",
        system_prompt: "",
        voice_id: "",
        goal: "",
    });

    // Persona configuration. Drives the layered system prompt on the
    // backend — one of three personas + campaign-level slot fields +
    // 1-3 agent names rotated per call.
    const [personaType, setPersonaType] = useState<PersonaType>("lead_gen");
    const [companyName, setCompanyName] = useState("");
    const [agentNamesRaw, setAgentNamesRaw] = useState("");
    const [slotValues, setSlotValues] = useState<Record<string, string>>({});

    const persona: PersonaSpec = PERSONAS.find((p) => p.value === personaType) ?? PERSONAS[0];

    function setSlot(key: string, value: string) {
        setSlotValues((prev) => ({ ...prev, [key]: value }));
    }

    // Reset slot values when persona changes — different personas need
    // different fields, and leftover values would confuse the backend.
    function changePersona(next: PersonaType) {
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

    // Fetch curated voices from AI Options
    useEffect(() => {
        async function fetchVoices() {
            try {
                setLoadingVoices(true);
                const [voicesResult, config] = await Promise.all([
                    aiOptionsApi.getVoices(),
                    aiOptionsApi.getConfig(),
                ]);
                const voiceList = voicesResult.voices;
                const providerVoices = voiceList.filter((voice) => voice.provider === config.tts_provider);
                setVoices(voiceList);
                setGlobalAiConfig(config);
                if (providerVoices.length > 0) {
                    setFormData((prev) => (
                        prev.voice_id && providerVoices.some((voice) => voice.id === prev.voice_id)
                            ? prev
                            : { ...prev, voice_id: providerVoices[0].id }
                    ));
                }
            } catch (err) {
                captureException(err, { area: "campaigns-new", kind: "voices" });
                setError("Failed to load voices.");
            } finally {
                setLoadingVoices(false);
            }
        }
        fetchVoices();
    }, []);

    function handleChange(
        e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
    ) {
        setFormData((prev) => ({
            ...prev,
            [e.target.name]: e.target.value,
        }));
    }

    // Preview a voice
    async function handlePreviewVoice(voiceId: string) {
        const voice = voices.find((item) => item.id === voiceId);
        try {
            setPreviewingVoiceId(voiceId);

            if (previewAudioRef.current) {
                previewAudioRef.current.pause();
                previewAudioRef.current.currentTime = 0;
                previewAudioRef.current = null;
            }

            if (voice?.preview_url) {
                const audio = new Audio(voice.preview_url);
                previewAudioRef.current = audio;
                audio.onended = () => {
                    if (previewAudioRef.current === audio) {
                        previewAudioRef.current = null;
                    }
                    setPreviewingVoiceId(null);
                };
                audio.onerror = () => {
                    if (previewAudioRef.current === audio) {
                        previewAudioRef.current = null;
                    }
                    setPreviewingVoiceId(null);
                };
                await audio.play();
                return;
            }

            const response = await aiOptionsApi.previewVoice({
                voice_id: voiceId,
                text: "Hello, I am your AI voice assistant. How can I help you today?",
            });

            // Play audio
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
                voice?.provider === "cartesia"
                || voice?.provider === "google"
                || voice?.provider === "deepgram"
                || voice?.provider === "elevenlabs"
                    ? 24000
                    : 16000;

            const audioContext = new AudioContext({ sampleRate });
            const audioBuffer = audioContext.createBuffer(1, audioArray.length, sampleRate);
            audioBuffer.getChannelData(0).set(audioArray);

            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            source.start();

            source.onended = () => {
                audioContext.close();
                setPreviewingVoiceId(null);
            };
        } catch (err) {
            captureException(err, { area: "campaigns-new", kind: "voice-preview" });
            setPreviewingVoiceId(null);
        }
    }

    useEffect(() => {
        return () => {
            if (previewAudioRef.current) {
                previewAudioRef.current.pause();
                previewAudioRef.current = null;
            }
        };
    }, []);

    const campaignVoices = globalAiConfig
        ? voices.filter((voice) => voice.provider === globalAiConfig.tts_provider)
        : voices;

    async function handleSubmit(e: React.FormEvent) {
        e.preventDefault();
        if (!formData.voice_id) {
            setError("Select a voice from the active global TTS provider before creating a campaign.");
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

        setLoading(true);
        setError("");

        try {
            const result = await dashboardApi.createCampaign({
                name: formData.name,
                description: formData.description || undefined,
                system_prompt: formData.system_prompt,
                voice_id: formData.voice_id,
                goal: formData.goal || undefined,
                persona_type: personaType,
                company_name: companyName.trim(),
                agent_names: agentNames,
                campaign_slots: buildCampaignSlots(),
            });

            router.push(`/campaigns/${result.campaign.id}`);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to create campaign");
        } finally {
            setLoading(false);
        }
    }

    return (
        <DashboardLayout title="Create Campaign" description="Set up a new voice campaign">
            <motion.div
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                className="mb-6"
            >
                <button
                    onClick={() => router.back()}
                    className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <ArrowLeft className="w-4 h-4" />
                    Back to campaigns
                </button>
            </motion.div>

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
                                disabled={loading}
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
                                disabled={loading}
                                rows={2}
                                className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                            />
                        </div>

                        {/* Goal */}
                        <div className="space-y-2">
                            <Label htmlFor="goal">Campaign Goal (optional)</Label>
                            <Input
                                id="goal"
                                name="goal"
                                placeholder="e.g., Schedule a demo, Collect feedback"
                                value={formData.goal}
                                onChange={handleChange}
                                disabled={loading}
                            />
                            <p className="text-xs text-muted-foreground">
                                Define what success looks like for each call
                            </p>
                        </div>

                        {/* Voice Selection - Updated to use AI Options voices */}
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
                            {loadingVoices ? (
                                <div className="flex items-center justify-center py-8">
                                    <RefreshCw className="w-5 h-5 animate-spin text-muted-foreground" />
                                    <span className="ml-2 text-muted-foreground">Loading voices...</span>
                                </div>
                            ) : (
                                campaignVoices.length === 0 ? (
                                    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-200">
                                        No voices are available for the current global TTS provider yet. Save a valid provider in AI Options first.
                                    </div>
                                ) : (
                                    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                                        {campaignVoices.map((voice) => (
                                            <div
                                                key={voice.id}
                                                onClick={() => setFormData((prev) => ({ ...prev, voice_id: voice.id }))}
                                                className={`relative p-3 rounded-lg border cursor-pointer transition-colors ${formData.voice_id === voice.id
                                                        ? "border-emerald-500 bg-emerald-500/20"
                                                        : "border-border bg-muted/30 hover:bg-muted/40"
                                                    }`}
                                            >
                                            {/* Play Preview Button */}
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
                                                    <RefreshCw className="w-4 h-4 animate-spin" style={{ color: voice.accent_color || "#10B981" }} />
                                                ) : (
                                                    <Play className="w-4 h-4" style={{ color: voice.accent_color || "#10B981" }} />
                                                )}
                                            </button>

                                            {/* Voice Info */}
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

                                                {/* Gender Tag */}
                                                <div className="mt-2 flex gap-1">
                                                    {voice.gender && (
                                                        <span className={`text-xs px-1.5 py-0.5 rounded ${voice.gender === "female"
                                                                ? "bg-pink-500/20 text-pink-400"
                                                                : "bg-blue-500/20 text-blue-400"
                                                            }`}>
                                                            {voice.gender}
                                                        </span>
                                                    )}
                                                    <span className="text-xs px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                                                        {(voice.language || "unknown").toUpperCase()}
                                                    </span>
                                                </div>
                                            </div>

                                            {/* Selected Indicator */}
                                            {formData.voice_id === voice.id && (
                                                <div className="absolute bottom-2 right-2">
                                                    <Check className="w-4 h-4 text-emerald-400" />
                                                </div>
                                            )}
                                            </div>
                                        ))}
                                    </div>
                                )
                            )}
                        </div>

                        {/* Persona picker — three roles sharing the same generic guardrails. */}
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
                                                disabled={loading}
                                                className="mt-0"
                                            />
                                            <span className="text-sm font-medium text-foreground">{p.title}</span>
                                        </div>
                                        <span className="text-xs text-muted-foreground">{p.summary}</span>
                                    </label>
                                ))}
                            </div>
                        </div>

                        {/* Company name + agent-name pool. */}
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
                                    disabled={loading}
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
                                    disabled={loading}
                                />
                                <p className="text-xs text-muted-foreground">
                                    Comma-separated. One is picked per call — supply 2–3 to rotate.
                                </p>
                            </div>
                        </div>

                        {/* Persona-specific slot fields. */}
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
                                            disabled={loading}
                                            rows={slot.kind === "textarea" ? 3 : 4}
                                            className="flex w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50"
                                        />
                                    ) : (
                                        <Input
                                            id={`slot-${slot.key}`}
                                            placeholder={slot.placeholder}
                                            value={slotValues[slot.key] ?? ""}
                                            onChange={(e) => setSlot(slot.key, e.target.value)}
                                            disabled={loading}
                                        />
                                    )}
                                    {slot.help ? (
                                        <p className="text-xs text-muted-foreground">{slot.help}</p>
                                    ) : null}
                                </div>
                            ))}
                        </div>

                        {/* Freeform "additional instructions" — appended after the persona block. */}
                        <div className="space-y-2">
                            <Label htmlFor="system_prompt">Additional instructions</Label>
                            <textarea
                                id="system_prompt"
                                name="system_prompt"
                                placeholder="Anything specific to this campaign that the generic rules and persona don't cover."
                                value={formData.system_prompt}
                                onChange={handleChange}
                                required
                                disabled={loading}
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
                            <Button type="submit" disabled={loading || !formData.voice_id}>
                                {loading ? (
                                    <>
                                        <Loader2 className="w-4 h-4 animate-spin" />
                                        Creating...
                                    </>
                                ) : (
                                    "Create Campaign"
                                )}
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                onClick={() => router.back()}
                                disabled={loading}
                            >
                                Cancel
                            </Button>
                        </div>
                    </form>
                </motion.div>
            </div>
        </DashboardLayout>
    );
}
