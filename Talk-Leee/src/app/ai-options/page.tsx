"use client";

import { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import {
    aiOptionsApi,
    AIProviderConfig,
    ProviderListResponse,
    VoiceInfo
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
    Save
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

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

export default function AIOptionsPage() {
    // State
    const [providers, setProviders] = useState<ProviderListResponse | null>(null);
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [config, setConfig] = useState<AIProviderConfig | null>(null);
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

    // TTS Provider filter state
    const [ttsProvider, setTtsProvider] = useState<string>("");

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
            setLatencyMetrics(prev => ({
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

    // Preview a specific voice by ID (for individual voice cards)
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
                    if (voicePreviewAudioRef.current === audio) {
                        voicePreviewAudioRef.current = null;
                    }
                    setPreviewingVoiceId(null);
                };
                audio.onerror = () => {
                    if (voicePreviewAudioRef.current === audio) {
                        voicePreviewAudioRef.current = null;
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

            // Guard: createBuffer throws if frame count is 0 (empty audio from provider).
            if (audioArray.length === 0) {
                throw new Error("This voice returned no audio — it may be deprecated or unavailable.");
            }

            // Keep playback sample-rate aligned with provider output to avoid speed/pitch artifacts.
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
        new Set([...(providers?.tts.providers ?? []), ...voices.map((voice) => voice.provider)])
    );
    const voiceNameCounts = useMemo(() => {
        const counts = new Map<string, number>();
        for (const voice of voices) {
            counts.set(voice.name, (counts.get(voice.name) ?? 0) + 1);
        }
        return counts;
    }, [voices]);

    const getDisplayVoiceName = useCallback((voice: VoiceInfo): string => {
        const duplicateCount = voiceNameCounts.get(voice.name) ?? 0;
        if (duplicateCount <= 1) return voice.name;
        const language = (voice.language || "unknown").toUpperCase();
        return `${voice.name} (${language})`;
    }, [voiceNameCounts]);
    const voicesForSelectedProvider = voices.filter((voice) => voice.provider === ttsProvider);
    const ttsModelsForSelectedProvider = getProviderTtsModels(ttsProvider, providers);

    return (
        <DashboardLayout title="AI Options" description="Configure LLM, STT, and TTS providers">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
                </div>
            ) : !providers || !config ? (
                <div className="space-y-4">
                    <div className="content-card border-red-500/30 bg-red-500/10">
                        <div className="flex items-center gap-3 text-red-400">
                            <AlertCircle className="w-5 h-5" />
                            <span>{error || "AI options failed to load from the backend."}</span>
                        </div>
                    </div>
                    <button
                        onClick={loadData}
                        className="flex items-center gap-2 px-4 py-2 rounded-lg bg-purple-500/20 hover:bg-purple-500/30 text-purple-200 border border-purple-500/30"
                    >
                        <RefreshCw className="w-4 h-4" />
                        Retry
                    </button>
                </div>
            ) : (
                <div className="space-y-8">
                    {/* Error Banner */}
                    <AnimatePresence>
                        {error && (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="content-card border-red-500/30 bg-red-500/10"
                            >
                                <div className="flex items-center gap-3 text-red-400">
                                    <AlertCircle className="w-5 h-5" />
                                    <span>{error}</span>
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Save Success Banner */}
                    <AnimatePresence>
                        {saveSuccess && (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="content-card border-emerald-500/30 bg-emerald-500/10"
                            >
                                <div className="flex items-center gap-3 text-emerald-400">
                                    <Check className="w-5 h-5" />
                                    <span>Configuration saved successfully!</span>
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Latency Advisory Warnings */}
                    <AnimatePresence>
                        {latencyWarnings.length > 0 && (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="space-y-2"
                            >
                                {latencyWarnings.map((w, i) => (
                                    <div
                                        key={i}
                                        className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300"
                                    >
                                        <span className="shrink-0 mt-0.5">⚠</span>
                                        <span>{w}</span>
                                    </div>
                                ))}
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* Provider Selection Grid */}
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                        {/* LLM Provider */}
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.1 }}
                            className="content-card group"
                        >
                            <div className="flex items-center gap-3 mb-6">
                                <div className="p-2 bg-purple-500/20 rounded-lg">
                                    <Cpu className="w-5 h-5 text-purple-400" />
                                </div>
                                <div>
                                    <h3 className="text-lg font-semibold text-white group-hover:text-gray-900 dark:group-hover:text-white">LLM Model</h3>
                                    <p className="text-sm text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">
                                        {providers?.llm.providers?.join(' / ') || 'Groq'}
                                    </p>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mb-2">Model</label>
                                    <select
                                        value={config.llm_model}
                                        onChange={(e) => {
                                            // Auto-set llm_provider from the selected model so a Gemini model
                                            // never gets saved with llm_provider="groq" (or vice-versa).
                                            const picked = providers?.llm.models.find(m => m.id === e.target.value);
                                            setConfig({
                                                ...config,
                                                llm_model: e.target.value,
                                                llm_provider: (picked?.provider as typeof config.llm_provider) || config.llm_provider,
                                            });
                                        }}
                                        className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white group-hover:text-gray-900 group-hover:bg-black/5 group-hover:border-black/10 dark:group-hover:text-white dark:group-hover:bg-white/5 dark:group-hover:border-white/10 focus:outline-none focus:border-purple-500/50"
                                    >
                                        {providers?.llm.models.map((model) => (
                                            <option key={model.id} value={model.id} className="bg-gray-900">
                                                {model.provider ? `[${model.provider}] ${model.name}` : model.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mb-2">
                                        Temperature: {config.llm_temperature}
                                    </label>
                                    <input
                                        type="range"
                                        min="0"
                                        max="2"
                                        step="0.1"
                                        value={config.llm_temperature}
                                        onChange={(e) => setConfig({ ...config, llm_temperature: parseFloat(e.target.value) })}
                                        className="w-full accent-purple-500"
                                    />
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mb-2">
                                        Max Tokens: {config.llm_max_tokens}
                                    </label>
                                    <input
                                        type="range"
                                        min="50"
                                        max="500"
                                        step="10"
                                        value={config.llm_max_tokens}
                                        onChange={(e) => setConfig({ ...config, llm_max_tokens: parseInt(e.target.value) })}
                                        className="w-full accent-purple-500"
                                    />
                                </div>

                                {/* Model Info */}
                                {providers?.llm.models.find(m => m.id === config.llm_model) && (
                                    <div className="p-3 bg-purple-500/10 rounded-lg border border-purple-500/20">
                                        <p className="text-sm text-purple-300">
                                            {providers.llm.models.find(m => m.id === config.llm_model)?.description}
                                        </p>
                                        <p className="text-xs text-purple-400 mt-1">
                                            Speed: {providers.llm.models.find(m => m.id === config.llm_model)?.speed}
                                        </p>
                                    </div>
                                )}
                            </div>
                        </motion.div>

                        {/* TTS Provider - Voice Selection Cards */}
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: 0.2 }}
                            className="content-card group md:col-span-2"
                        >
                            <div className="flex items-center justify-between mb-6">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-emerald-500/20 rounded-lg">
                                        <Volume2 className="w-5 h-5 text-emerald-400" />
                                    </div>
                                    <div>
                                        <h3 className="text-lg font-semibold text-white group-hover:text-gray-900 dark:group-hover:text-white">TTS Voice ({voicesForSelectedProvider.length} available)</h3>
                                        <p className="text-sm text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">Select a voice for your AI agent</p>
                                    </div>
                                </div>

                                {/* Provider Selector */}
                                <div className="flex gap-2 p-1 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg border border-white/10 group-hover:border-black/10 dark:group-hover:border-white/10">
                                    {availableTtsProviders.map((providerName) => {
                                        const isActive = ttsProvider === providerName;
                                        return (
                                            <button
                                                key={providerName}
                                                onClick={() => {
                                                    setTtsProvider(providerName);
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
                                                }}
                                                className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                                                    isActive
                                                        ? "bg-gradient-to-r from-purple-500 to-blue-500 text-white shadow-lg shadow-purple-500/25"
                                                        : "bg-transparent text-gray-300 hover:bg-white/10"
                                                }`}
                                            >
                                                {providerName} ({voices.filter((v) => v.provider === providerName).length})
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>

                            <div className="mb-4">
                                <label className="block text-sm font-medium text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mb-2">
                                    TTS Model
                                </label>
                                <select
                                    value={config.tts_model}
                                    onChange={(e) => setConfig({ ...config, tts_model: e.target.value })}
                                    className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white group-hover:text-gray-900 group-hover:bg-black/5 group-hover:border-black/10 dark:group-hover:text-white dark:group-hover:bg-white/5 dark:group-hover:border-white/10 focus:outline-none focus:border-emerald-500/50"
                                >
                                    {ttsModelsForSelectedProvider.map((model) => (
                                        <option key={model.id} value={model.id} className="bg-gray-900">
                                            {model.name}
                                        </option>
                                    ))}
                                </select>
                                {ttsModelsForSelectedProvider.find((model) => model.id === config.tts_model) && (
                                    <div className="mt-3 p-3 bg-emerald-500/10 rounded-lg border border-emerald-500/20">
                                        <p className="text-sm text-emerald-300">
                                            {ttsModelsForSelectedProvider.find((model) => model.id === config.tts_model)?.description}
                                        </p>
                                        <p className="text-xs text-emerald-400 mt-1">
                                            Speed: {ttsModelsForSelectedProvider.find((model) => model.id === config.tts_model)?.speed || "n/a"}
                                        </p>
                                    </div>
                                )}
                            </div>

                            {/* Voice Cards Grid - Filtered by Provider */}
                            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 mb-4">
                                {voicesForSelectedProvider.length === 0 && (
                                    <div className="col-span-full rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
                                        {ttsProvider === "elevenlabs" && elevenLabsError
                                            ? <>
                                                <span className="font-semibold">ElevenLabs API error:</span>{" "}
                                                {elevenLabsError.includes("401")
                                                    ? "API key is invalid or expired. Update ELEVENLABS_API_KEY in your .env file and restart the server."
                                                    : elevenLabsError}
                                              </>
                                            : `No voices are available for "${ttsProvider}". Check that the provider key is configured and reload this page.`}
                                    </div>
                                )}
                                {voicesForSelectedProvider.map((voice) => (
                                        <div
                                            key={voice.id}
                                            onClick={() => setConfig({
                                                ...config,
                                                tts_voice_id: voice.id,
                                                tts_provider: voice.provider,
                                                tts_model: ttsModelsForSelectedProvider.some((model) => model.id === config.tts_model)
                                                    ? config.tts_model
                                                    : getDefaultTtsModel(voice.provider, providers),
                                                tts_sample_rate: getDefaultTtsSampleRate(voice.provider),
                                            })}
                                            className={`relative p-3 rounded-lg border cursor-pointer transition-all hover:scale-[1.02] ${config.tts_voice_id === voice.id
                                                ? "border-emerald-500 bg-emerald-500/20"
                                                : "border-white/20 bg-white/5 hover:bg-white/10 group-hover:border-black/10 group-hover:bg-black/5 group-hover:hover:bg-black/10 dark:group-hover:border-white/20 dark:group-hover:bg-white/5 dark:group-hover:hover:bg-white/10"
                                                }`}
                                        >
                                            {/* Play Preview Button */}
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handlePreviewVoiceById(voice.id);
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
                                                    <p className="font-medium text-sm text-white group-hover:text-gray-900 dark:group-hover:text-white">{getDisplayVoiceName(voice)}</p>
                                                </div>
                                                <p className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mt-1 line-clamp-2">
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
                                                    <span className="text-xs px-1.5 py-0.5 rounded bg-white/10 text-gray-300">
                                                        {(voice.language || "unknown").toUpperCase()}
                                                    </span>
                                                </div>
                                            </div>

                                            {/* Selected Indicator */}
                                            {config.tts_voice_id === voice.id && (
                                                <div className="absolute bottom-2 right-2">
                                                    <Check className="w-4 h-4 text-emerald-400" />
                                                </div>
                                            )}
                                        </div>
                                    ))}
                            </div>

                            {/* Selected Voice Preview */}
                            {(() => {
                                const selectedVoice = voices.find(v => v.id === config.tts_voice_id);
                                if (!selectedVoice || selectedVoice.provider !== ttsProvider) return null;
                                return (
                                    <div className="p-4 bg-emerald-500/10 rounded-lg border border-emerald-500/30">
                                        <div className="flex items-center gap-3">
                                            <div
                                                className="w-10 h-10 rounded-full flex items-center justify-center"
                                                style={{ backgroundColor: (selectedVoice.accent_color || "#10B981") + "40" }}
                                            >
                                                <Volume2 className="w-5 h-5" style={{ color: selectedVoice.accent_color || "#10B981" }} />
                                            </div>
                                            <div className="flex-1">
                                            <p className="text-sm font-medium text-white group-hover:text-gray-900 dark:group-hover:text-white">{getDisplayVoiceName(selectedVoice)}</p>
                                            <p className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">{selectedVoice.description}</p>
                                            </div>
                                            <button
                                                onClick={() => handlePreviewVoiceById(selectedVoice.id)}
                                                disabled={previewingVoiceId === selectedVoice.id}
                                            className="px-4 py-2 bg-emerald-500/30 hover:bg-emerald-500/40 rounded-lg text-emerald-400 hover:text-white text-sm flex items-center gap-2 transition-[transform,background-color,color] duration-150 ease-out hover:scale-[1.02] active:scale-[0.99]"
                                            >
                                                {previewingVoiceId === selectedVoice.id ? (
                                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                                ) : (
                                                    <Play className="w-4 h-4" />
                                                )}
                                                <span>Preview Selected</span>
                                            </button>
                                        </div>
                                    </div>
                                );
                            })()}
                        </motion.div>
                    </div>

                    {/* Latency Metrics */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.4 }}
                        className="content-card group"
                    >
                        <div className="flex items-center justify-between mb-6">
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-yellow-500/20 rounded-lg">
                                    <Zap className="w-5 h-5 text-yellow-400" />
                                </div>
                                <div>
                                    <h3 className="text-lg font-semibold text-white group-hover:text-gray-900 dark:group-hover:text-white">Latency Metrics</h3>
                                    <p className="text-sm text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">Real-time performance tracking</p>
                                </div>
                            </div>
                            <button
                                onClick={handleRunBenchmark}
                                disabled={benchmarking}
                                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-purple-500 to-blue-500 hover:from-purple-600 hover:to-blue-600 rounded-lg text-white font-medium transition-all shadow-lg shadow-purple-500/25 hover:scale-[1.02] active:scale-[0.99] disabled:opacity-50"
                            >
                                {benchmarking ? (
                                    <RefreshCw className="w-4 h-4 animate-spin" />
                                ) : (
                                    <Zap className="w-4 h-4" />
                                )}
                                <span>Run Benchmark</span>
                            </button>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-purple-400">
                                    {latencyMetrics.llm_first_token_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mt-1">LLM First Token (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-purple-400">
                                    {latencyMetrics.llm_total_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mt-1">LLM Total (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-emerald-400">
                                    {latencyMetrics.tts_first_audio_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mt-1">TTS First Audio (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-emerald-400">
                                    {latencyMetrics.tts_total_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mt-1">TTS Total (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-yellow-400">
                                    {latencyMetrics.total_pipeline_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mt-1">Total Pipeline (ms)</p>
                            </div>
                        </div>
                    </motion.div>

                    {/* LLM Test Section */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.5 }}
                        className="content-card group"
                    >
                        <div className="flex items-center gap-3 mb-6">
                            <div className="p-2 bg-white/10 rounded-lg">
                                <MessageSquare className="w-5 h-5 text-white group-hover:text-gray-900 dark:group-hover:text-white" />
                            </div>
                            <div>
                                <h3 className="text-lg font-semibold text-white group-hover:text-gray-900 dark:group-hover:text-white">Test LLM</h3>
                                <p className="text-sm text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">Send a message to test the selected model</p>
                            </div>
                        </div>

                        <div className="space-y-4">
                            <div className="flex gap-4">
                                <input
                                    type="text"
                                    value={testMessage}
                                    onChange={(e) => setTestMessage(e.target.value)}
                                    onKeyDown={(e) => e.key === "Enter" && handleTestLLM()}
                                    placeholder="Type a message to test the LLM..."
                                    className="flex-1 bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white group-hover:text-gray-900 group-hover:bg-black/5 group-hover:border-black/10 dark:group-hover:text-white dark:group-hover:bg-white/5 dark:group-hover:border-white/10 placeholder-gray-500 transition-[background-color,border-color,color] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:border-white/20 focus:outline-none focus:border-purple-500/50"
                                />
                                <button
                                    onClick={handleTestLLM}
                                    disabled={testing || !testMessage.trim()}
                                    className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-purple-500 to-blue-500 hover:from-purple-600 hover:to-blue-600 rounded-lg text-white font-medium transition-all shadow-lg shadow-purple-500/25 hover:scale-[1.02] active:scale-[0.99]"
                                >
                                    {testing ? (
                                        <RefreshCw className="w-4 h-4 animate-spin" />
                                    ) : (
                                        <Send className="w-4 h-4" />
                                    )}
                                    <span>Send</span>
                                </button>
                            </div>

                            {testResponse && (
                                <div className="p-4 bg-purple-500/10 border border-purple-500/20 rounded-lg">
                                    <p className="text-sm text-gray-300 whitespace-pre-wrap">{testResponse}</p>
                                </div>
                            )}
                        </div>
                    </motion.div>

                    {/* Save Button */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.6 }}
                        className="flex justify-end"
                    >
                        <button
                            onClick={handleSaveConfig}
                            className="flex items-center gap-2 px-8 py-3 bg-gradient-to-r from-purple-500 to-blue-500 hover:from-purple-600 hover:to-blue-600 rounded-lg text-white font-medium transition-all shadow-lg shadow-purple-500/25 hover:scale-[1.02] active:scale-[0.99]"
                        >
                            <Save className="w-5 h-5" />
                            <span>Save Configuration</span>
                        </button>
                    </motion.div>
                </div>
            )}
        </DashboardLayout>
    );
}
