"use client";

import { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { ApplyToCampaignsModal } from "@/components/campaigns/apply-to-campaigns-modal";
import {
    aiOptionsApi,
    AIProviderConfig,
    ProviderListResponse,
    VoiceInfo,
} from "@/lib/ai-options-api";
import {
    Cpu,
    Volume2,
    Zap,
    Play,
    Send,
    RefreshCw,
    Check,
    AlertCircle,
    MessageSquare,
    Save,
    Sparkles,
    SlidersHorizontal,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import {
    RadialKnob,
    Segmented,
    Equalizer,
    normalizeAccent,
    ACCENT_META,
    type AccentBucket,
} from "@/components/ai-options/controls";

interface LatencyMetrics {
    llm_first_token_ms?: number;
    llm_total_ms?: number;
    tts_first_audio_ms?: number;
    tts_total_ms?: number;
    total_pipeline_ms?: number;
}

const GOOGLE_TTS_MODEL = "Chirp3-HD";
const DEEPGRAM_TTS_MODEL = "aura-2";
const ELEVENLABS_TTS_MODEL = "eleven_flash_v2_5";
const CARTESIA_TTS_MODEL = "sonic-3";

function getFallbackDefaultTtsModel(provider: string): string {
    if (provider === "cartesia") return CARTESIA_TTS_MODEL;
    if (provider === "deepgram") return DEEPGRAM_TTS_MODEL;
    if (provider === "elevenlabs") return ELEVENLABS_TTS_MODEL;
    if (provider === "google") return GOOGLE_TTS_MODEL;
    return GOOGLE_TTS_MODEL;
}

function getProviderTtsModels(provider: string, providers: ProviderListResponse | null): Array<ProviderListResponse["tts"]["models"][number]> {
    return (providers?.tts.models ?? []).filter((model) => {
        if (!model.provider) return true;
        return model.provider === provider;
    });
}

function getDefaultTtsModel(provider: string, providers: ProviderListResponse | null): string {
    const providerModels = getProviderTtsModels(provider, providers);
    if (providerModels.length > 0) {
        const stableModel = providerModels.find((model) => !model.is_preview);
        return (stableModel ?? providerModels[0]).id;
    }
    return getFallbackDefaultTtsModel(provider);
}

function getDefaultTtsSampleRate(provider: string): number {
    if (provider === "cartesia" || provider === "google" || provider === "deepgram" || provider === "elevenlabs") return 24000;
    return 16000;
}

function dedupeVoicesById(input: VoiceInfo[]): VoiceInfo[] {
    const map = new Map<string, VoiceInfo>();
    for (const voice of input) {
        if (!map.has(voice.id)) map.set(voice.id, voice);
    }
    return Array.from(map.values());
}

// ── small presentational helpers (in-file, page-specific) ────────
function GlassCard({ children, className = "", delay = 0 }: { children: React.ReactNode; className?: string; delay?: number }) {
    return (
        <motion.section
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay, duration: 0.45, ease: "easeOut" }}
            className={`relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.07] to-white/[0.02] p-5 sm:p-6 shadow-[0_10px_40px_-15px_rgba(0,0,0,0.7)] backdrop-blur-xl ${className}`}
        >
            {children}
        </motion.section>
    );
}

function SectionHeader({ icon, color, title, subtitle, right }: { icon: React.ReactNode; color: string; title: string; subtitle?: string; right?: React.ReactNode }) {
    return (
        <div className="mb-5 flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-xl border border-white/10" style={{ background: `${color}22`, boxShadow: `inset 0 0 18px -6px ${color}` }}>
                    <span style={{ color }}>{icon}</span>
                </div>
                <div>
                    <h3 className="text-base font-semibold text-white sm:text-lg">{title}</h3>
                    {subtitle && <p className="text-xs text-zinc-400 sm:text-sm">{subtitle}</p>}
                </div>
            </div>
            {right}
        </div>
    );
}

export default function AIOptionsPage() {
    // State
    const [providers, setProviders] = useState<ProviderListResponse | null>(null);
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [config, setConfig] = useState<AIProviderConfig | null>(null);

    function updateVoiceTuningField<K extends keyof NonNullable<AIProviderConfig["voice_tuning"]>>(
        field: K,
        value: NonNullable<AIProviderConfig["voice_tuning"]>[K],
    ) {
        setConfig((prev) => {
            if (!prev) return prev;
            const current = prev.voice_tuning ?? {};
            return { ...prev, voice_tuning: { ...current, [field]: value } };
        });
    }

    function resetVoiceTuningField(field: keyof NonNullable<AIProviderConfig["voice_tuning"]>) {
        setConfig((prev) => {
            if (!prev) return prev;
            const current = { ...(prev.voice_tuning ?? {}) };
            delete current[field];
            return { ...prev, voice_tuning: current };
        });
    }
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [saveSuccess, setSaveSuccess] = useState(false);
    const [latencyWarnings, setLatencyWarnings] = useState<string[]>([]);
    const [elevenLabsError, setElevenLabsError] = useState<string | null>(null);

    // Testing state
    const [testMessage, setTestMessage] = useState("");
    const [testResponse, setTestResponse] = useState("");
    const [testing, setTesting] = useState(false);
    const [latencyMetrics, setLatencyMetrics] = useState<LatencyMetrics>({});
    const [benchmarking, setBenchmarking] = useState(false);

    const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);

    // TTS provider + accent filter state
    const [ttsProvider, setTtsProvider] = useState<string>("");
    const [accentFilter, setAccentFilter] = useState<"All" | AccentBucket>("All");
    const [applyModal, setApplyModal] = useState<{ provider: string; voiceId: string; voiceLabel?: string } | null>(null);

    const voicePreviewAudioRef = useRef<HTMLAudioElement | null>(null);

    useEffect(() => {
        loadData();
    }, []);

    async function loadData() {
        try {
            setLoading(true);
            setError("");

            const [providersData, voicesResult, configData] = await Promise.all([
                aiOptionsApi.getProviders(),
                aiOptionsApi.getVoices(),
                aiOptionsApi.getConfig(),
            ]);

            setElevenLabsError(voicesResult.elevenlabs_error ?? null);
            const uniqueVoices = dedupeVoicesById(voicesResult.voices);
            const providerVoices = uniqueVoices.filter((voice) => voice.provider === configData.tts_provider);
            const providerModels = getProviderTtsModels(configData.tts_provider, providersData);
            const normalizedConfig: AIProviderConfig = {
                ...configData,
                tts_model: providerModels.some((model) => model.id === configData.tts_model)
                    ? configData.tts_model
                    : getDefaultTtsModel(configData.tts_provider, providersData),
                tts_sample_rate: getDefaultTtsSampleRate(configData.tts_provider),
                tts_voice_id: providerVoices.some((voice) => voice.id === configData.tts_voice_id)
                    ? configData.tts_voice_id
                    : (providerVoices[0]?.id ?? configData.tts_voice_id),
            };

            setProviders(providersData);
            setVoices(uniqueVoices);
            setConfig(normalizedConfig);
            const providerOptions = new Set([
                ...providersData.tts.providers,
                ...uniqueVoices.map((voice) => voice.provider),
            ]);
            const initialProvider = providerOptions.has(normalizedConfig.tts_provider)
                ? normalizedConfig.tts_provider
                : (uniqueVoices[0]?.provider ?? normalizedConfig.tts_provider);
            setTtsProvider(initialProvider);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load AI options");
        } finally {
            setLoading(false);
        }
    }

    async function handleSaveConfig() {
        if (!config) return;
        setLatencyWarnings([]);
        try {
            const normalizedConfig: AIProviderConfig = {
                ...config,
                tts_model: config.tts_model || getDefaultTtsModel(config.tts_provider, providers),
                tts_sample_rate: getDefaultTtsSampleRate(config.tts_provider),
            };
            const { config: saved, latency_warnings } = await aiOptionsApi.saveConfig(normalizedConfig);
            setConfig({
                ...saved,
                tts_model: saved.tts_model || getDefaultTtsModel(saved.tts_provider, providers),
                tts_sample_rate: getDefaultTtsSampleRate(saved.tts_provider),
            });
            setTtsProvider(saved.tts_provider);
            setSaveSuccess(true);
            setApplyModal({
                provider: saved.tts_provider,
                voiceId: saved.tts_voice_id,
                voiceLabel: voices.find((v) => v.id === saved.tts_voice_id)?.name,
            });
            setLatencyWarnings(latency_warnings);
            setTimeout(() => setSaveSuccess(false), 3000);
            if (latency_warnings.length > 0) {
                setTimeout(() => setLatencyWarnings([]), 8000);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to save configuration");
        }
    }

    async function handleTestLLM() {
        if (!config) return;
        if (!testMessage.trim()) return;

        try {
            setTesting(true);
            setError("");

            const response = await aiOptionsApi.testLLM({
                model: config.llm_model,
                message: testMessage,
                temperature: config.llm_temperature,
                max_tokens: config.llm_max_tokens,
            });

            setTestResponse(response.response);
            setLatencyMetrics((prev) => ({
                ...prev,
                llm_first_token_ms: response.first_token_ms,
                llm_total_ms: response.latency_ms,
            }));
        } catch (err) {
            setError(err instanceof Error ? err.message : "LLM test failed");
        } finally {
            setTesting(false);
        }
    }

    async function handlePreviewVoiceById(voiceId: string) {
        const selectedVoice = voices.find((voice) => voice.id === voiceId);
        try {
            setPreviewingVoiceId(voiceId);
            setError("");

            if (voicePreviewAudioRef.current) {
                voicePreviewAudioRef.current.pause();
                voicePreviewAudioRef.current.currentTime = 0;
                voicePreviewAudioRef.current = null;
            }

            if (selectedVoice?.preview_url) {
                const audio = new Audio(selectedVoice.preview_url);
                voicePreviewAudioRef.current = audio;
                audio.onended = () => {
                    if (voicePreviewAudioRef.current === audio) voicePreviewAudioRef.current = null;
                    setPreviewingVoiceId(null);
                };
                audio.onerror = () => {
                    if (voicePreviewAudioRef.current === audio) voicePreviewAudioRef.current = null;
                    setPreviewingVoiceId(null);
                };
                await audio.play();
                return;
            }

            const response = await aiOptionsApi.previewVoice({
                voice_id: voiceId,
                text: "Hello, I am your AI voice assistant. How can I help you today?",
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

            if (audioArray.length === 0) {
                throw new Error("This voice returned no audio — it may be deprecated or unavailable.");
            }

            const sampleRate =
                selectedVoice?.provider === "cartesia"
                || selectedVoice?.provider === "google"
                || selectedVoice?.provider === "deepgram"
                || selectedVoice?.provider === "elevenlabs"
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
            setError(err instanceof Error ? err.message : "Voice preview failed");
            setPreviewingVoiceId(null);
        }
    }

    async function handleRunBenchmark() {
        if (!config) return;
        try {
            setBenchmarking(true);
            setError("");
            const result = await aiOptionsApi.runBenchmark(config);
            setLatencyMetrics(result);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Benchmark failed");
        } finally {
            setBenchmarking(false);
        }
    }

    useEffect(() => {
        return () => {
            if (voicePreviewAudioRef.current) {
                voicePreviewAudioRef.current.pause();
                voicePreviewAudioRef.current = null;
            }
        };
    }, []);

    const availableTtsProviders = Array.from(
        new Set([...(providers?.tts.providers ?? []), ...voices.map((voice) => voice.provider)]),
    );
    const voiceNameCounts = useMemo(() => {
        const counts = new Map<string, number>();
        for (const voice of voices) counts.set(voice.name, (counts.get(voice.name) ?? 0) + 1);
        return counts;
    }, [voices]);

    const getDisplayVoiceName = useCallback((voice: VoiceInfo): string => {
        const duplicateCount = voiceNameCounts.get(voice.name) ?? 0;
        if (duplicateCount <= 1) return voice.name;
        const language = (voice.language || "unknown").toUpperCase();
        return `${voice.name} (${language})`;
    }, [voiceNameCounts]);

    const voicesForSelectedProvider = useMemo(
        () => voices.filter((voice) => voice.provider === ttsProvider),
        [voices, ttsProvider],
    );
    const ttsModelsForSelectedProvider = getProviderTtsModels(ttsProvider, providers);

    // Accent buckets actually present for the current provider's voices.
    const accentBuckets = useMemo(() => {
        const order: AccentBucket[] = ["US", "UK", "AU", "Other"];
        const present = new Set(voicesForSelectedProvider.map((v) => normalizeAccent(v.accent, v.language)));
        return order.filter((b) => present.has(b));
    }, [voicesForSelectedProvider]);

    const filteredVoices = useMemo(() => {
        if (accentFilter === "All") return voicesForSelectedProvider;
        return voicesForSelectedProvider.filter((v) => normalizeAccent(v.accent, v.language) === accentFilter);
    }, [voicesForSelectedProvider, accentFilter]);

    function selectProvider(providerName: string) {
        setTtsProvider(providerName);
        setAccentFilter("All");
        setConfig((prev) => {
            if (!prev) return prev;
            const providerVoices = voices.filter((voice) => voice.provider === providerName);
            const nextVoiceId = providerVoices.some((voice) => voice.id === prev.tts_voice_id)
                ? prev.tts_voice_id
                : (providerVoices[0]?.id ?? prev.tts_voice_id);
            return {
                ...prev,
                tts_provider: providerName,
                tts_model: getDefaultTtsModel(providerName, providers),
                tts_voice_id: nextVoiceId,
                tts_sample_rate: getDefaultTtsSampleRate(providerName),
            };
        });
    }

    const llmModelInfo = providers?.llm.models.find((m) => m.id === config?.llm_model);
    const ttsModelInfo = ttsModelsForSelectedProvider.find((model) => model.id === config?.tts_model);

    return (
        <DashboardLayout title="AI Options" description="Configure LLM, STT, and TTS providers">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-400" />
                </div>
            ) : !providers || !config ? (
                <div className="space-y-4">
                    <div className="rounded-2xl border border-red-500/30 bg-red-500/10 p-5">
                        <div className="flex items-center gap-3 text-red-400">
                            <AlertCircle className="w-5 h-5" />
                            <span>{error || "AI options failed to load from the backend."}</span>
                        </div>
                    </div>
                    <button onClick={loadData} className="flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-500/20 hover:bg-purple-500/30 text-purple-200 border border-purple-500/30">
                        <RefreshCw className="w-4 h-4" />
                        Retry
                    </button>
                </div>
            ) : (
                <div className="space-y-6">
                    {/* Hero */}
                    <motion.div
                        initial={{ opacity: 0, y: -8 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-purple-600/25 via-blue-600/15 to-emerald-600/20 p-6"
                    >
                        <motion.div
                            aria-hidden
                            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-purple-500/30 blur-3xl"
                            animate={{ scale: [1, 1.15, 1], opacity: [0.5, 0.8, 0.5] }}
                            transition={{ duration: 8, repeat: Infinity, ease: "easeInOut" }}
                        />
                        <motion.div
                            aria-hidden
                            className="pointer-events-none absolute -left-10 bottom-0 h-40 w-40 rounded-full bg-emerald-500/25 blur-3xl"
                            animate={{ scale: [1, 1.2, 1], opacity: [0.4, 0.7, 0.4] }}
                            transition={{ duration: 10, repeat: Infinity, ease: "easeInOut", delay: 1 }}
                        />
                        <div className="relative flex items-center gap-3">
                            <div className="grid h-12 w-12 place-items-center rounded-2xl border border-white/20 bg-white/10 backdrop-blur">
                                <Sparkles className="h-6 w-6 text-white" />
                            </div>
                            <div>
                                <h2 className="text-xl font-bold text-white sm:text-2xl">AI Options</h2>
                                <p className="text-sm text-white/70">Tune the brain, the voice, and the rhythm of every call.</p>
                            </div>
                        </div>
                    </motion.div>

                    {/* Banners */}
                    <AnimatePresence>
                        {error && (
                            <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="rounded-xl border border-red-500/30 bg-red-500/10 p-4">
                                <div className="flex items-center gap-3 text-red-400"><AlertCircle className="w-5 h-5" /><span>{error}</span></div>
                            </motion.div>
                        )}
                    </AnimatePresence>
                    <AnimatePresence>
                        {saveSuccess && (
                            <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
                                <div className="flex items-center gap-3 text-emerald-400"><Check className="w-5 h-5" /><span>Configuration saved successfully!</span></div>
                            </motion.div>
                        )}
                    </AnimatePresence>
                    <AnimatePresence>
                        {latencyWarnings.length > 0 && (
                            <motion.div initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} className="space-y-2">
                                {latencyWarnings.map((w, i) => (
                                    <div key={i} className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
                                        <span className="shrink-0 mt-0.5">⚠</span><span>{w}</span>
                                    </div>
                                ))}
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* LLM */}
                    <GlassCard delay={0.05}>
                        <SectionHeader
                            icon={<Cpu className="h-5 w-5" />}
                            color="#a855f7"
                            title="LLM Model"
                            subtitle={providers?.llm.providers?.join(" / ") || "Groq"}
                        />
                        <div className="grid gap-6 lg:grid-cols-[1fr_auto]">
                            <div className="space-y-4">
                                <div>
                                    <label className="mb-2 block text-sm font-medium text-zinc-400">Model</label>
                                    <select
                                        value={config.llm_model}
                                        onChange={(e) => {
                                            const picked = providers?.llm.models.find((m) => m.id === e.target.value);
                                            setConfig({ ...config, llm_model: e.target.value, llm_provider: (picked?.provider as typeof config.llm_provider) || config.llm_provider });
                                        }}
                                        className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white outline-none transition focus:border-purple-500/60"
                                    >
                                        {providers?.llm.models.map((model) => (
                                            <option key={model.id} value={model.id} className="bg-zinc-900">
                                                {model.provider ? `[${model.provider}] ${model.name}` : model.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                                {llmModelInfo && (
                                    <div className="rounded-lg border border-purple-500/20 bg-purple-500/10 p-3">
                                        <p className="text-sm text-purple-200">{llmModelInfo.description}</p>
                                        <p className="mt-1 text-xs text-purple-300/80">Speed: {llmModelInfo.speed ?? "n/a"}</p>
                                    </div>
                                )}
                            </div>
                            {/* Circular knobs */}
                            <div className="flex items-center justify-center gap-6 rounded-xl border border-white/5 bg-black/20 px-4 py-3">
                                <RadialKnob
                                    label="Temp"
                                    value={config.llm_temperature}
                                    min={0}
                                    max={2}
                                    step={0.1}
                                    color="#a855f7"
                                    format={(v) => v.toFixed(1)}
                                    hint="creativity"
                                    onChange={(v) => setConfig({ ...config, llm_temperature: v })}
                                />
                                <RadialKnob
                                    label="Tokens"
                                    value={config.llm_max_tokens}
                                    min={50}
                                    max={500}
                                    step={10}
                                    color="#3b82f6"
                                    hint="max length"
                                    onChange={(v) => setConfig({ ...config, llm_max_tokens: v })}
                                />
                            </div>
                        </div>
                    </GlassCard>

                    {/* TTS */}
                    <GlassCard delay={0.1}>
                        <SectionHeader
                            icon={<Volume2 className="h-5 w-5" />}
                            color="#10b981"
                            title={`TTS Voice · ${filteredVoices.length} available`}
                            subtitle="Pick a provider, accent, and voice for your agent"
                            right={
                                <Segmented
                                    color="#10b981"
                                    size="sm"
                                    value={ttsProvider}
                                    onChange={selectProvider}
                                    options={availableTtsProviders.map((p) => ({
                                        value: p,
                                        label: <span className="capitalize">{p} <span className="opacity-60">({voices.filter((v) => v.provider === p).length})</span></span>,
                                    }))}
                                />
                            }
                        />

                        <div className="mb-4 grid gap-3 sm:grid-cols-2">
                            <div>
                                <label className="mb-2 block text-sm font-medium text-zinc-400">TTS Model</label>
                                <select
                                    value={config.tts_model}
                                    onChange={(e) => setConfig({ ...config, tts_model: e.target.value })}
                                    className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white outline-none transition focus:border-emerald-500/60"
                                >
                                    {ttsModelsForSelectedProvider.map((model) => (
                                        <option key={model.id} value={model.id} className="bg-zinc-900">{model.name}</option>
                                    ))}
                                </select>
                            </div>
                            {/* Accent filter */}
                            <div>
                                <label className="mb-2 block text-sm font-medium text-zinc-400">Accent</label>
                                <Segmented
                                    color="#10b981"
                                    size="sm"
                                    value={accentFilter}
                                    onChange={(v) => setAccentFilter(v as "All" | AccentBucket)}
                                    options={[
                                        { value: "All", label: "All accents" },
                                        ...accentBuckets.map((b) => ({ value: b, label: <span>{ACCENT_META[b].flag} {ACCENT_META[b].label}</span> })),
                                    ]}
                                />
                            </div>
                        </div>

                        {ttsModelInfo && (
                            <div className="mb-4 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-3">
                                <p className="text-sm text-emerald-200">{ttsModelInfo.description}</p>
                                <p className="mt-1 text-xs text-emerald-300/80">Speed: {ttsModelInfo.speed || "n/a"}</p>
                            </div>
                        )}

                        {/* Voice grid */}
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                            {filteredVoices.length === 0 && (
                                <div className="col-span-full rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
                                    {ttsProvider === "elevenlabs" && elevenLabsError
                                        ? <><span className="font-semibold">ElevenLabs API error:</span>{" "}{elevenLabsError.includes("401") ? "API key is invalid or expired. Update ELEVENLABS_API_KEY and restart the server." : elevenLabsError}</>
                                        : voicesForSelectedProvider.length > 0
                                            ? `No ${accentFilter !== "All" ? ACCENT_META[accentFilter as AccentBucket].label : ""} voices for "${ttsProvider}". Try another accent.`
                                            : `No voices are available for "${ttsProvider}". Check the provider key and reload.`}
                                </div>
                            )}
                            {filteredVoices.map((voice) => {
                                const selected = config.tts_voice_id === voice.id;
                                const accent = normalizeAccent(voice.accent, voice.language);
                                const ac = voice.accent_color || "#10B981";
                                const isPlaying = previewingVoiceId === voice.id;
                                return (
                                    <motion.div
                                        key={voice.id}
                                        onClick={() => setConfig({
                                            ...config,
                                            tts_voice_id: voice.id,
                                            tts_provider: voice.provider,
                                            tts_model: ttsModelsForSelectedProvider.some((m) => m.id === config.tts_model) ? config.tts_model : getDefaultTtsModel(voice.provider, providers),
                                            tts_sample_rate: getDefaultTtsSampleRate(voice.provider),
                                        })}
                                        whileHover={{ y: -3, rotateX: 5, rotateY: -5 }}
                                        style={{ transformPerspective: 700 }}
                                        className={`group relative cursor-pointer rounded-xl border p-3 transition-colors ${selected ? "border-emerald-400/70 bg-emerald-500/15 shadow-[0_0_0_1px_rgba(16,185,129,0.4),0_10px_30px_-12px_rgba(16,185,129,0.5)]" : "border-white/10 bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.06]"}`}
                                    >
                                        <button
                                            type="button"
                                            onClick={(e) => { e.stopPropagation(); handlePreviewVoiceById(voice.id); }}
                                            disabled={isPlaying}
                                            className="absolute right-2 top-2 grid h-8 w-8 place-items-center rounded-full transition-transform hover:scale-110"
                                            style={{ backgroundColor: ac + "26" }}
                                            aria-label="Preview voice"
                                        >
                                            {isPlaying ? <Equalizer active color={ac} /> : <Play className="h-4 w-4" style={{ color: ac }} />}
                                        </button>
                                        <div className="pr-10">
                                            <div className="flex items-center gap-2">
                                                <div className="grid h-6 w-6 place-items-center rounded-full" style={{ backgroundColor: ac + "26" }}>
                                                    <Volume2 className="h-3 w-3" style={{ color: ac }} />
                                                </div>
                                                <p className="truncate text-sm font-medium text-white">{getDisplayVoiceName(voice)}</p>
                                            </div>
                                            <p className="mt-1 line-clamp-2 text-xs text-zinc-400">{voice.description}</p>
                                            <div className="mt-2 flex flex-wrap gap-1">
                                                <span className="rounded bg-white/10 px-1.5 py-0.5 text-xs text-zinc-200" title={`${ACCENT_META[accent].label} accent`}>
                                                    {ACCENT_META[accent].flag} {ACCENT_META[accent].label}
                                                </span>
                                                {voice.gender && (
                                                    <span className={`rounded px-1.5 py-0.5 text-xs ${voice.gender === "female" ? "bg-pink-500/20 text-pink-300" : "bg-blue-500/20 text-blue-300"}`}>{voice.gender}</span>
                                                )}
                                            </div>
                                        </div>
                                        {selected && <div className="absolute bottom-2 right-2"><Check className="h-4 w-4 text-emerald-400" /></div>}
                                    </motion.div>
                                );
                            })}
                        </div>

                        {/* Selected preview bar */}
                        {(() => {
                            const selectedVoice = voices.find((v) => v.id === config.tts_voice_id);
                            if (!selectedVoice || selectedVoice.provider !== ttsProvider) return null;
                            const ac = selectedVoice.accent_color || "#10B981";
                            const isPlaying = previewingVoiceId === selectedVoice.id;
                            return (
                                <div className="mt-4 flex items-center gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
                                    <div className="grid h-10 w-10 place-items-center rounded-full" style={{ backgroundColor: ac + "33" }}>
                                        <Volume2 className="h-5 w-5" style={{ color: ac }} />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <p className="truncate text-sm font-medium text-white">{getDisplayVoiceName(selectedVoice)}</p>
                                        <p className="truncate text-xs text-zinc-400">{selectedVoice.description}</p>
                                    </div>
                                    <button
                                        onClick={() => handlePreviewVoiceById(selectedVoice.id)}
                                        disabled={isPlaying}
                                        className="flex items-center gap-2 rounded-lg bg-emerald-500/30 px-4 py-2 text-sm text-emerald-200 transition hover:bg-emerald-500/40 hover:text-white"
                                    >
                                        {isPlaying ? <Equalizer active /> : <Play className="h-4 w-4" />}
                                        <span>Preview Selected</span>
                                    </button>
                                </div>
                            );
                        })()}
                    </GlassCard>

                    {/* Latency */}
                    <GlassCard delay={0.15}>
                        <SectionHeader
                            icon={<Zap className="h-5 w-5" />}
                            color="#eab308"
                            title="Latency Metrics"
                            subtitle="Real-time pipeline performance"
                            right={
                                <button onClick={handleRunBenchmark} disabled={benchmarking} className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-purple-500 to-blue-500 px-4 py-2 font-medium text-white shadow-lg shadow-purple-500/25 transition hover:scale-[1.02] active:scale-[0.99] disabled:opacity-50">
                                    {benchmarking ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Zap className="h-4 w-4" />}
                                    <span>Run Benchmark</span>
                                </button>
                            }
                        />
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                            {([
                                ["LLM First Token", latencyMetrics.llm_first_token_ms, "#a855f7"],
                                ["LLM Total", latencyMetrics.llm_total_ms, "#a855f7"],
                                ["TTS First Audio", latencyMetrics.tts_first_audio_ms, "#10b981"],
                                ["TTS Total", latencyMetrics.tts_total_ms, "#10b981"],
                                ["Total Pipeline", latencyMetrics.total_pipeline_ms, "#eab308"],
                            ] as const).map(([label, val, color], i) => (
                                <motion.div
                                    key={label}
                                    initial={{ opacity: 0, scale: 0.9 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    transition={{ delay: 0.05 * i }}
                                    whileHover={{ y: -3 }}
                                    className="rounded-xl border border-white/5 bg-black/20 p-4 text-center"
                                >
                                    <p className="text-2xl font-bold tabular-nums" style={{ color }}>{val !== undefined ? val.toFixed(0) : "—"}</p>
                                    <p className="mt-1 text-xs text-zinc-400">{label} <span className="opacity-60">(ms)</span></p>
                                </motion.div>
                            ))}
                        </div>
                    </GlassCard>

                    {/* Voice tuning */}
                    <GlassCard delay={0.2}>
                        <SectionHeader icon={<SlidersHorizontal className="h-5 w-5" />} color="#38bdf8" title="Voice tuning" subtitle="Optional · falls back to defaults when unset" />
                        <p className="mb-4 text-xs text-zinc-400">
                            Conversational rhythm tuning for this tenant. Each field is optional — “Reset to default” clears the override. Changes apply on the next call after Save.
                        </p>
                        {config && (() => {
                            const tuning = config.voice_tuning ?? {};
                            const eot = tuning.stt_eot_threshold;
                            const timeout = tuning.stt_eot_timeout_ms;
                            const eager = tuning.stt_eager_eot_threshold;
                            const eagerExplicit = "stt_eager_eot_threshold" in tuning;
                            const minConf = tuning.turn_0_min_confidence;
                            const minChars = tuning.turn_0_min_alpha_chars;
                            const resetLink = "text-[11px] text-zinc-500 underline hover:text-zinc-300";
                            const rangeCls = "mt-1 w-full accent-sky-500";
                            return (
                                <div className="grid gap-5 md:grid-cols-2">
                                    <div>
                                        <div className="flex items-center justify-between text-xs">
                                            <span className="font-medium text-white">End-of-turn confidence <span className="ml-1 text-zinc-500">default 0.85</span></span>
                                            <span className="font-mono text-emerald-400">{eot !== undefined ? eot.toFixed(2) : "—"}</span>
                                        </div>
                                        <input type="range" min={0.5} max={0.9} step={0.05} value={eot ?? 0.85} onChange={(e) => updateVoiceTuningField("stt_eot_threshold", parseFloat(e.target.value))} className={rangeCls} />
                                        {eot !== undefined && <button type="button" onClick={() => resetVoiceTuningField("stt_eot_threshold")} className={resetLink}>Reset to default</button>}
                                    </div>
                                    <div>
                                        <div className="flex items-center justify-between text-xs">
                                            <span className="font-medium text-white">End-of-turn silence timeout (ms) <span className="ml-1 text-zinc-500">default 500</span></span>
                                            <span className="font-mono text-emerald-400">{timeout !== undefined ? timeout : "—"}</span>
                                        </div>
                                        <input type="number" min={500} max={10000} step={100} value={timeout ?? 500} onChange={(e) => { const v = parseInt(e.target.value, 10); if (!Number.isNaN(v)) updateVoiceTuningField("stt_eot_timeout_ms", v); }} className="mt-1 w-full rounded border border-white/10 bg-white/5 px-3 py-2 text-sm text-white" />
                                        {timeout !== undefined && <button type="button" onClick={() => resetVoiceTuningField("stt_eot_timeout_ms")} className={resetLink}>Reset to default</button>}
                                    </div>
                                    <div>
                                        <div className="flex items-center justify-between text-xs">
                                            <span className="font-medium text-white">Eager-mode threshold <span className="ml-1 text-zinc-500">default 0.7</span></span>
                                            <span className="font-mono text-emerald-400">{!eagerExplicit ? "—" : eager === null ? "disabled" : (eager as number).toFixed(2)}</span>
                                        </div>
                                        <input type="range" min={0.3} max={0.9} step={0.05} value={(eager ?? 0.7) as number} onChange={(e) => updateVoiceTuningField("stt_eager_eot_threshold", parseFloat(e.target.value))} disabled={eager === null} className={`${rangeCls} disabled:opacity-40`} />
                                        <div className="mt-1 flex flex-wrap items-center gap-3">
                                            <label className="flex items-center gap-1 text-[11px] text-zinc-400">
                                                <input type="checkbox" checked={eager === null} onChange={(e) => updateVoiceTuningField("stt_eager_eot_threshold", e.target.checked ? null : 0.7)} className="accent-sky-500" />
                                                Disable eager mode
                                            </label>
                                            {eagerExplicit && <button type="button" onClick={() => resetVoiceTuningField("stt_eager_eot_threshold")} className={resetLink}>Reset to default</button>}
                                        </div>
                                    </div>
                                    <div>
                                        <div className="flex items-center justify-between text-xs">
                                            <span className="font-medium text-white">Turn-0 minimum confidence <span className="ml-1 text-zinc-500">default 0.4</span></span>
                                            <span className="font-mono text-emerald-400">{minConf !== undefined ? minConf.toFixed(2) : "—"}</span>
                                        </div>
                                        <input type="range" min={0} max={1} step={0.05} value={minConf ?? 0.4} onChange={(e) => updateVoiceTuningField("turn_0_min_confidence", parseFloat(e.target.value))} className={rangeCls} />
                                        {minConf !== undefined && <button type="button" onClick={() => resetVoiceTuningField("turn_0_min_confidence")} className={resetLink}>Reset to default</button>}
                                    </div>
                                    <div>
                                        <div className="flex items-center justify-between text-xs">
                                            <span className="font-medium text-white">Turn-0 minimum alpha chars <span className="ml-1 text-zinc-500">default 2</span></span>
                                            <span className="font-mono text-emerald-400">{minChars !== undefined ? minChars : "—"}</span>
                                        </div>
                                        <input type="number" min={1} max={10} step={1} value={minChars ?? 2} onChange={(e) => { const v = parseInt(e.target.value, 10); if (!Number.isNaN(v)) updateVoiceTuningField("turn_0_min_alpha_chars", v); }} className="mt-1 w-full rounded border border-white/10 bg-white/5 px-3 py-2 text-sm text-white" />
                                        {minChars !== undefined && <button type="button" onClick={() => resetVoiceTuningField("turn_0_min_alpha_chars")} className={resetLink}>Reset to default</button>}
                                    </div>
                                </div>
                            );
                        })()}
                    </GlassCard>

                    {/* Test LLM */}
                    <GlassCard delay={0.25}>
                        <SectionHeader icon={<MessageSquare className="h-5 w-5" />} color="#ffffff" title="Test LLM" subtitle="Send a message to the selected model" />
                        <div className="space-y-4">
                            <div className="flex flex-col gap-3 sm:flex-row">
                                <input
                                    type="text"
                                    value={testMessage}
                                    onChange={(e) => setTestMessage(e.target.value)}
                                    onKeyDown={(e) => e.key === "Enter" && handleTestLLM()}
                                    placeholder="Type a message to test the LLM…"
                                    className="flex-1 rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-zinc-500 outline-none transition focus:border-purple-500/60"
                                />
                                <button onClick={handleTestLLM} disabled={testing || !testMessage.trim()} className="flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-purple-500 to-blue-500 px-6 py-3 font-medium text-white shadow-lg shadow-purple-500/25 transition hover:scale-[1.02] active:scale-[0.99] disabled:opacity-50">
                                    {testing ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}<span>Send</span>
                                </button>
                            </div>
                            {testResponse && (
                                <div className="rounded-lg border border-purple-500/20 bg-purple-500/10 p-4">
                                    <p className="whitespace-pre-wrap text-sm text-zinc-200">{testResponse}</p>
                                </div>
                            )}
                        </div>
                    </GlassCard>

                    {/* Save */}
                    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="sticky bottom-4 z-10 flex justify-end">
                        <button onClick={handleSaveConfig} className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-purple-500 to-blue-500 px-8 py-3 font-semibold text-white shadow-[0_10px_30px_-8px_rgba(168,85,247,0.7)] transition hover:scale-[1.03] active:scale-[0.98]">
                            <Save className="h-5 w-5" /><span>Save Configuration</span>
                        </button>
                    </motion.div>
                </div>
            )}
            <ApplyToCampaignsModal
                open={!!applyModal}
                provider={applyModal?.provider ?? ""}
                voiceId={applyModal?.voiceId ?? ""}
                voiceLabel={applyModal?.voiceLabel}
                onClose={() => setApplyModal(null)}
            />
        </DashboardLayout>
    );
}
