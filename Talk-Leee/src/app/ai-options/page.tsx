"use client";

import { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import {
    aiOptionsApi,
    AIProviderConfig,
    ProviderListResponse,
    VoiceInfo
} from "@/lib/ai-options-api";
import { apiBaseUrl } from "@/lib/env";
import { captureException } from "@/lib/monitoring";
import {
    Cpu,
    Mic,
    Volume2,
    Zap,
    Play,
    Send,
    RefreshCw,
    Check,
    AlertCircle,
    MessageSquare,
    Save,
    Phone,
    PhoneOff,
    User,
    Bot
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

interface LatencyMetrics {
    llm_first_token_ms?: number;
    llm_total_ms?: number;
    tts_first_audio_ms?: number;
    tts_total_ms?: number;
    total_pipeline_ms?: number;
}

interface LiveCallMessage {
    role: "user" | "assistant" | "system";
    content: string;
    timestamp: number;
}

interface LiveCallState {
    isActive: boolean;
    sessionId: string | null;
    callId: string | null;
    conversationState: string;
    agentName: string;
    companyName: string;
    messages: LiveCallMessage[];
    latency: {
        llm_ms?: number;
        tts_ms?: number;
    };
}

const GOOGLE_TTS_MODEL = "Chirp3-HD";
const DEEPGRAM_TTS_MODEL = "aura-2";

function getDefaultTtsModel(provider: string): string {
    if (provider === "deepgram") return DEEPGRAM_TTS_MODEL;
    if (provider === "google") return GOOGLE_TTS_MODEL;
    return GOOGLE_TTS_MODEL;
}

function getDefaultTtsSampleRate(provider: string): number {
    if (provider === "google" || provider === "deepgram") return 24000;
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

    // Testing state
    const [testMessage, setTestMessage] = useState("");
    const [testResponse, setTestResponse] = useState("");
    const [testing, setTesting] = useState(false);
    const [latencyMetrics, setLatencyMetrics] = useState<LatencyMetrics>({});
    const [benchmarking, setBenchmarking] = useState(false);

    const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);

    // TTS Provider filter state
    const [ttsProvider, setTtsProvider] = useState<string>("");

    // Live test call state
    const [liveCall, setLiveCall] = useState<LiveCallState>({
        isActive: false,
        sessionId: null,
        callId: null,
        conversationState: "idle",
        agentName: "",
        companyName: "",
        messages: [],
        latency: {}
    });

    const [liveCallConnecting, setLiveCallConnecting] = useState(false);

    // WebSocket ref
    const wsRef = useRef<WebSocket | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const audioInitPromiseRef = useRef<Promise<void> | null>(null);
    const playbackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
    const nextPlaybackTimeRef = useRef(0);
    const ttsSampleRateRef = useRef<number>(24000);
    const ttsAudioFormatRef = useRef<"s16le" | "f32le">("s16le");
    const awaitingPlaybackCompleteRef = useRef(false);

    // Microphone capture refs
    const micStreamRef = useRef<MediaStream | null>(null);
    const micAudioContextRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);
    const micMuteGainRef = useRef<GainNode | null>(null);

    useEffect(() => {
        loadData();
    }, []);

    async function loadData() {
        try {
            setLoading(true);
            setError("");

            const [providersData, voicesData, configData] = await Promise.all([
                aiOptionsApi.getProviders(),
                aiOptionsApi.getVoices(),
                aiOptionsApi.getConfig(),
            ]);

            const uniqueVoices = dedupeVoicesById(voicesData);
            const providerVoices = uniqueVoices.filter((voice) => voice.provider === configData.tts_provider);
            const normalizedConfig: AIProviderConfig = {
                ...configData,
                tts_model: getDefaultTtsModel(configData.tts_provider),
                tts_sample_rate: getDefaultTtsSampleRate(configData.tts_provider),
                tts_voice_id: providerVoices.some((voice) => voice.id === configData.tts_voice_id)
                    ? configData.tts_voice_id
                    : (providerVoices[0]?.id ?? configData.tts_voice_id),
            };

            setProviders(providersData);
            setVoices(uniqueVoices);
            setConfig(normalizedConfig);
            const voiceProviders = new Set(uniqueVoices.map((voice) => voice.provider));
            const initialProvider = voiceProviders.has(normalizedConfig.tts_provider)
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
        try {
            const normalizedConfig: AIProviderConfig = {
                ...config,
                tts_model: getDefaultTtsModel(config.tts_provider),
                tts_sample_rate: getDefaultTtsSampleRate(config.tts_provider),
            };
            const saved = await aiOptionsApi.saveConfig(normalizedConfig);
            setConfig(saved);
            setSaveSuccess(true);
            setTimeout(() => setSaveSuccess(false), 3000);
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
        try {
            setPreviewingVoiceId(voiceId);
            setError("");

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

            // Keep playback sample-rate aligned with provider output to avoid speed/pitch artifacts.
            const selectedVoice = voices.find((voice) => voice.id === voiceId);
            const sampleRate =
                selectedVoice?.provider === "google" || selectedVoice?.provider === "deepgram"
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

    const initializeAudioPlayer = useCallback(async () => {
        if (audioInitPromiseRef.current) {
            return audioInitPromiseRef.current;
        }

        audioInitPromiseRef.current = (async () => {
            try {
                if (!audioContextRef.current || audioContextRef.current.state === "closed") {
                    audioContextRef.current = new AudioContext({
                        latencyHint: "interactive",
                    });
                    nextPlaybackTimeRef.current = 0;
                }

                const ctx = audioContextRef.current;

                if (ctx.state === "suspended") {
                    await ctx.resume();
                }
            } finally {
                // Allow retry if init fails.
                setTimeout(() => {
                    audioInitPromiseRef.current = null;
                }, 100);
            }
        })();

        return audioInitPromiseRef.current;
    }, []);

    const queueAudioChunk = useCallback((buffer: ArrayBuffer, sampleRate: number, format: "s16le" | "f32le") => {
        const ctx = audioContextRef.current;
        if (!ctx) return;

        const float32 =
            format === "f32le"
                ? new Float32Array(buffer)
                : (() => {
                    const pcm16 = new Int16Array(buffer);
                    const out = new Float32Array(pcm16.length);
                    for (let i = 0; i < pcm16.length; i++) {
                        out[i] = pcm16[i] / 32768.0;
                    }
                    return out;
                })();

        if (float32.length === 0) return;

        const audioBuffer = ctx.createBuffer(1, float32.length, sampleRate);
        audioBuffer.getChannelData(0).set(float32);

        const source = ctx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(ctx.destination);

        const leadTimeSeconds = 0.08;
        const startAt = Math.max(
            ctx.currentTime + leadTimeSeconds,
            nextPlaybackTimeRef.current || 0,
        );

        source.onended = () => {
            playbackSourcesRef.current.delete(source);
            if (
                awaitingPlaybackCompleteRef.current &&
                playbackSourcesRef.current.size === 0 &&
                wsRef.current?.readyState === WebSocket.OPEN
            ) {
                awaitingPlaybackCompleteRef.current = false;
                wsRef.current.send(JSON.stringify({ type: "playback_complete" }));
            }
        };

        playbackSourcesRef.current.add(source);
        source.start(startAt);
        nextPlaybackTimeRef.current = startAt + audioBuffer.duration;
    }, []);

    const resetAudioPlayer = useCallback(() => {
        awaitingPlaybackCompleteRef.current = false;
        playbackSourcesRef.current.forEach((source) => {
            try {
                source.stop();
            } catch {
                // no-op
            }
        });
        playbackSourcesRef.current.clear();
        nextPlaybackTimeRef.current = 0;
    }, []);

    const cleanupAudioPlayer = useCallback(() => {
        awaitingPlaybackCompleteRef.current = false;
        playbackSourcesRef.current.forEach((source) => {
            try {
                source.stop();
            } catch {
                // no-op
            }
        });
        playbackSourcesRef.current.clear();
        nextPlaybackTimeRef.current = 0;

        if (audioContextRef.current) {
            try {
                void audioContextRef.current.close();
            } catch {
                // no-op
            }
            audioContextRef.current = null;
        }

        audioInitPromiseRef.current = null;
    }, []);

    // Live test call - WebSocket connection and handlers
    function startLiveCall() {
        if (!config) {
            setError("Configuration not loaded");
            return;
        }
        if (wsRef.current) return;

        setLiveCallConnecting(true);
        setError("");

        const sessionId = `session-${Date.now()}`;
        const apiUrl = apiBaseUrl();
        const u = new URL(apiUrl);
        u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
        u.pathname = `${u.pathname.replace(/\/$/, "")}/ws/ai-test/${sessionId}`;
        const wsUrl = u.toString();

        try {
            const ws = new WebSocket(wsUrl);
            ws.binaryType = "arraybuffer";
            wsRef.current = ws;

            ws.onopen = () => {
                // Send config message to start session
                ws.send(JSON.stringify({
                    type: "config",
                    config: config
                }));
            };

            ws.onmessage = async (event) => {
                if (event.data instanceof Blob || event.data instanceof ArrayBuffer) {
                    // Binary audio data - queue for playback
                    const arrayBuffer = event.data instanceof Blob
                        ? await event.data.arrayBuffer()
                        : event.data;
                    if (!audioContextRef.current) {
                        await initializeAudioPlayer();
                    }
                    queueAudioChunk(arrayBuffer, ttsSampleRateRef.current, ttsAudioFormatRef.current);
                } else {
                    // JSON message
                    const data = JSON.parse(event.data);
                    handleLiveCallMessage(data);
                }
            };

            ws.onerror = (err) => {
                captureException(err, { area: "ai-options", kind: "websocket" });
                setError("Connection error");
                endLiveCall();
            };

            ws.onclose = () => {
                endLiveCall();
            };
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to connect");
            setLiveCallConnecting(false);
        }
    }

    const handleLiveCallMessage = (data: unknown) => {
        if (!data || typeof data !== "object") return;
        const payload = data as Record<string, unknown>;
        const type = typeof payload.type === "string" ? payload.type : "";
        switch (type) {
            case "ready":
                if (typeof payload.sample_rate === "number" && payload.sample_rate > 0) {
                    ttsSampleRateRef.current = payload.sample_rate;
                } else if (config) {
                    ttsSampleRateRef.current = config.tts_sample_rate;
                } else {
                    ttsSampleRateRef.current = 24000;
                }
                if (payload.audio_format === "f32le" || payload.audio_format === "s16le") {
                    ttsAudioFormatRef.current = payload.audio_format;
                } else {
                    ttsAudioFormatRef.current = "s16le";
                }
                setLiveCall(prev => ({
                    ...prev,
                    isActive: true,
                    sessionId: typeof payload.session_id === "string" ? payload.session_id : prev.sessionId,
                    callId: typeof payload.call_id === "string" ? payload.call_id : prev.callId,
                    conversationState: typeof payload.state === "string" ? payload.state : prev.conversationState,
                    agentName: typeof payload.agent_name === "string" ? payload.agent_name : prev.agentName,
                    companyName: typeof payload.company_name === "string" ? payload.company_name : prev.companyName
                }));
                setLiveCallConnecting(false);
                // Auto-start microphone capture when call connects (like real phone call)
                startMicrophone();
                break;

            case "transcript":
                // User speech transcribed (voice mode) - add user message when final
                if (payload.is_final === true && typeof payload.text === "string" && payload.text.trim()) {
                    setLiveCall(prev => ({
                        ...prev,
                        messages: [...prev.messages, {
                            role: "user",
                            content: payload.text as string,
                            timestamp: Date.now()
                        }]
                    }));
                }
                break;

            case "llm_response":
                {
                const text = typeof payload.text === "string" ? payload.text : "";
                const latencyMs = typeof payload.latency_ms === "number" ? payload.latency_ms : undefined;
                setLiveCall(prev => ({
                    ...prev,
                    messages: text
                        ? [...prev.messages, { role: "assistant", content: text, timestamp: Date.now() }]
                        : prev.messages,
                    latency: {
                        ...prev.latency,
                        llm_ms: latencyMs ?? prev.latency.llm_ms
                    }
                }));
                }
                break;

            case "state_change":
                setLiveCall(prev => ({
                    ...prev,
                    conversationState: typeof payload.state === "string" ? payload.state : prev.conversationState
                }));
                break;

            case "turn_complete":
                setLiveCall(prev => ({
                    ...prev,
                    latency: {
                        llm_ms: typeof payload.llm_latency_ms === "number" ? payload.llm_latency_ms : prev.latency.llm_ms,
                        tts_ms: typeof payload.tts_latency_ms === "number" ? payload.tts_latency_ms : prev.latency.tts_ms
                    }
                }));
                break;

            case "tts_audio_complete":
                awaitingPlaybackCompleteRef.current = true;
                if (
                    playbackSourcesRef.current.size === 0 &&
                    wsRef.current?.readyState === WebSocket.OPEN
                ) {
                    awaitingPlaybackCompleteRef.current = false;
                    wsRef.current.send(JSON.stringify({ type: "playback_complete" }));
                }
                break;

            case "barge_in":
                // User started speaking - stop TTS playback immediately
                resetAudioPlayer();
                break;

            case "tts_interrupted":
                // TTS was interrupted due to barge-in
                resetAudioPlayer();
                break;

            case "error":
                setError(typeof payload.message === "string" ? payload.message : "Unknown error");
                break;
        }
    };



    // Start microphone capture - auto-starts when call connects
    const startMicrophone = async () => {
        try {
            // Request microphone access
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });

            micStreamRef.current = stream;

            // Create audio context at 16kHz for Deepgram
            const audioContext = new AudioContext({ sampleRate: 16000 });
            micAudioContextRef.current = audioContext;

            // Create source from microphone
            const source = audioContext.createMediaStreamSource(stream);

            // Create script processor to capture raw audio (4096 buffer size)
            const processor = audioContext.createScriptProcessor(4096, 1, 1);
            processorRef.current = processor;

            // Keep processor clocked without local mic monitoring in speakers.
            const muteGain = audioContext.createGain();
            muteGain.gain.value = 0;
            micMuteGainRef.current = muteGain;

            processor.onaudioprocess = (event) => {
                // Always output silence to prevent local mic monitoring artifacts.
                const outputData = event.outputBuffer.getChannelData(0);
                outputData.fill(0);

                if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

                // Get PCM data from input buffer
                const inputData = event.inputBuffer.getChannelData(0);

                // Convert Float32 to Int16 (PCM 16-bit) for Deepgram
                const pcmData = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    const s = Math.max(-1, Math.min(1, inputData[i]));
                    pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }

                // Send binary audio to WebSocket
                wsRef.current.send(pcmData.buffer);
            };

            // Connect the audio graph
            source.connect(processor);
            processor.connect(muteGain);
            muteGain.connect(audioContext.destination);
        } catch (err) {
            captureException(err, { area: "ai-options", kind: "microphone" });
            setError("Microphone access denied or unavailable");
        }
    };

    // Stop microphone capture
    const stopMicrophone = () => {
        if (processorRef.current) {
            processorRef.current.disconnect();
            processorRef.current = null;
        }

        if (micMuteGainRef.current) {
            micMuteGainRef.current.disconnect();
            micMuteGainRef.current = null;
        }

        if (micAudioContextRef.current) {
            micAudioContextRef.current.close();
            micAudioContextRef.current = null;
        }

        if (micStreamRef.current) {
            micStreamRef.current.getTracks().forEach(track => track.stop());
            micStreamRef.current = null;
        }
    };

    const endLiveCall = useCallback(() => {
        // Stop microphone capture
        stopMicrophone();

        if (wsRef.current) {
            wsRef.current.send(JSON.stringify({ type: "end_call" }));
            wsRef.current.close();
            wsRef.current = null;
        }
        cleanupAudioPlayer();
        ttsSampleRateRef.current = 24000;
        ttsAudioFormatRef.current = "s16le";

        setLiveCall({
            isActive: false,
            sessionId: null,
            callId: null,
            conversationState: "idle",
            agentName: "",
            companyName: "",
            messages: [],
            latency: {}
        });
        setLiveCallConnecting(false);
    }, [cleanupAudioPlayer]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
            cleanupAudioPlayer();
        };
    }, [cleanupAudioPlayer]);

    const availableTtsProviders = Array.from(new Set(voices.map((voice) => voice.provider)));
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

                    {/* Live Test Call - Test Full Pipeline */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card group border-2 border-orange-500/30 bg-gradient-to-br from-orange-500/5 to-transparent"
                    >
                        <div className="flex items-center justify-between mb-6">
                            <div className="flex items-center gap-3">
                                <div className="p-3 bg-orange-500/20 rounded-lg">
                                    <Phone className="w-6 h-6 text-orange-400" />
                                </div>
                                <div>
                                    <h3 className="text-xl font-bold text-white group-hover:text-gray-900 dark:group-hover:text-white">Live Test Call</h3>
                                    <p className="text-sm text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">
                                        Test the <span className="text-orange-400 font-medium">exact same pipeline</span> used for real calls
                                    </p>
                                </div>
                            </div>

                            {!liveCall.isActive ? (
                                <button
                                    onClick={startLiveCall}
                                    disabled={liveCallConnecting}
                                    className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-purple-500 to-blue-500 hover:from-purple-600 hover:to-blue-600 rounded-lg text-white font-medium transition-all shadow-lg shadow-purple-500/25 hover:scale-[1.02] active:scale-[0.99] disabled:opacity-50"
                                >
                                    {liveCallConnecting ? (
                                        <RefreshCw className="w-5 h-5 animate-spin" />
                                    ) : (
                                        <Phone className="w-5 h-5" />
                                    )}
                                    <span>{liveCallConnecting ? "Connecting..." : "Start Test Call"}</span>
                                </button>
                            ) : (
                                <button
                                    onClick={endLiveCall}
                                    className="flex items-center gap-2 px-6 py-3 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 rounded-lg text-red-400 hover:text-white font-medium transition-all hover:scale-[1.02] active:scale-[0.99]"
                                >
                                    <PhoneOff className="w-5 h-5" />
                                    <span>End Call</span>
                                </button>
                            )}
                        </div>

                        {liveCall.isActive && (
                            <div className="space-y-4">
                                {/* Network Latency Bar - Always visible above the call */}
                                <div className="flex items-center justify-center gap-6 p-3 bg-gradient-to-r from-purple-500/10 via-emerald-500/10 to-blue-500/10 rounded-lg border border-white/10 group-hover:border-black/10 dark:group-hover:border-white/10">
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-purple-500 rounded-full" />
                                        <span className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">LLM:</span>
                                        <span className="text-sm font-mono font-bold text-purple-400">
                                            {liveCall.latency.llm_ms ? `${liveCall.latency.llm_ms.toFixed(0)}ms` : '--'}
                                        </span>
                                    </div>
                                    <div className="h-4 w-px bg-white/20 group-hover:bg-black/10 dark:group-hover:bg-white/20" />
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-emerald-500 rounded-full" />
                                        <span className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">TTS:</span>
                                        <span className="text-sm font-mono font-bold text-emerald-400">
                                            {liveCall.latency.tts_ms ? `${liveCall.latency.tts_ms.toFixed(0)}ms` : '--'}
                                        </span>
                                    </div>
                                    <div className="h-4 w-px bg-white/20 group-hover:bg-black/10 dark:group-hover:bg-white/20" />
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-yellow-500 rounded-full" />
                                        <span className="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">Total:</span>
                                        <span className="text-sm font-mono font-bold text-yellow-400">
                                            {(liveCall.latency.llm_ms && liveCall.latency.tts_ms)
                                                ? `${(liveCall.latency.llm_ms + liveCall.latency.tts_ms).toFixed(0)}ms`
                                                : '--'}
                                        </span>
                                    </div>
                                </div>

                                {/* Call Info Bar */}
                                <div className="flex items-center justify-between p-3 bg-orange-500/10 rounded-lg border border-orange-500/20">
                                    <div className="flex items-center gap-4">
                                        <div className="flex items-center gap-2">
                                            <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                                            <span className="text-sm text-gray-300 group-hover:text-gray-700 dark:group-hover:text-gray-300">
                                                <span className="text-orange-400">{liveCall.agentName || "—"}</span> from{" "}
                                                <span className="text-orange-400">{liveCall.companyName || "—"}</span>
                                            </span>
                                        </div>
                                        <div className="h-4 w-px bg-white/20 group-hover:bg-black/10 dark:group-hover:bg-white/20" />
                                        <span className="text-xs px-2 py-1 bg-orange-500/20 rounded text-orange-400 uppercase font-medium">
                                            {liveCall.conversationState}
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2 text-xs">
                                        <span className="px-2 py-1 bg-blue-500/20 rounded text-blue-400">
                                            Provider: {config.tts_provider}
                                        </span>
                                        <span className="px-2 py-1 bg-purple-500/20 rounded text-purple-400">
                                            {config.tts_sample_rate / 1000}kHz
                                        </span>
                                    </div>
                                </div>


                                {/* Microphone Status Indicator */}
                                <div className="flex items-center justify-center gap-3 p-4 bg-green-500/10 rounded-lg border border-green-500/20">
                                    <div className="relative">
                                        <Mic className="w-6 h-6 text-green-400" />
                                        <div className="absolute -top-1 -right-1 w-3 h-3 bg-green-500 rounded-full animate-pulse" />
                                    </div>
                                    <span className="text-green-400 font-medium">Microphone Active - Speak to Respond</span>
                                </div>

                                {/* Chat Messages */}
                                <div className="h-64 overflow-y-auto p-4 bg-black/20 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg border border-white/5 group-hover:border-black/10 dark:group-hover:border-white/5 space-y-3">
                                    {liveCall.messages.length === 0 ? (
                                        <div className="flex flex-col items-center justify-center h-full text-gray-500 group-hover:text-gray-600 dark:group-hover:text-gray-500 gap-2">
                                            <Mic className="w-8 h-8 text-gray-600 group-hover:text-gray-500" />
                                            <p>Listening... Speak into your microphone</p>
                                            <p className="text-xs text-gray-600 group-hover:text-gray-500 dark:group-hover:text-gray-600">Your speech will be transcribed automatically</p>
                                        </div>
                                    ) : (
                                        liveCall.messages.map((msg, idx) => (
                                            <div
                                                key={idx}
                                                className={`flex items-start gap-3 ${msg.role === "user" ? "justify-end" : ""}`}
                                            >
                                                {msg.role === "assistant" && (
                                                    <div className="p-2 bg-orange-500/20 rounded-lg shrink-0">
                                                        <Bot className="w-4 h-4 text-orange-400" />
                                                    </div>
                                                )}
                                                <div
                                                    className={`max-w-[75%] p-3 rounded-lg ${msg.role === "user"
                                                        ? "bg-blue-500/20 border border-blue-500/30 text-blue-100 group-hover:bg-blue-500/10 group-hover:border-blue-500/20 group-hover:text-blue-900 dark:group-hover:text-blue-100"
                                                        : "bg-white/5 border border-white/10 text-gray-200 group-hover:bg-black/5 group-hover:border-black/10 group-hover:text-gray-800 dark:group-hover:bg-white/5 dark:group-hover:border-white/10 dark:group-hover:text-gray-200 dark:group-hover:hover:bg-white/10"
                                                        }`}
                                                >
                                                    <p className="text-sm">{msg.content}</p>
                                                </div>
                                                {msg.role === "user" && (
                                                    <div className="p-2 bg-blue-500/20 rounded-lg shrink-0">
                                                        <User className="w-4 h-4 text-blue-400" />
                                                    </div>
                                                )}
                                            </div>
                                        ))
                                    )}
                                </div>

                                <p className="text-xs text-gray-500 group-hover:text-gray-600 dark:group-hover:text-gray-500 text-center">
                                    🎤 <span className="text-green-400">Voice-only mode</span> - Uses the{" "}
                                    <span className="text-orange-400">exact same VoicePipelineService</span>,{" "}
                                    <span className="text-purple-400">PromptManager</span>, and{" "}
                                    <span className="text-blue-400">ConversationEngine</span> as real phone calls.
                                </p>
                            </div>
                        )}
                    </motion.div>

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
                                    <p className="text-sm text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400">Groq AI</p>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-400 mb-2">Model</label>
                                    <select
                                        value={config.llm_model}
                                        onChange={(e) => setConfig({ ...config, llm_model: e.target.value })}
                                        className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white group-hover:text-gray-900 group-hover:bg-black/5 group-hover:border-black/10 dark:group-hover:text-white dark:group-hover:bg-white/5 dark:group-hover:border-white/10 focus:outline-none focus:border-purple-500/50"
                                    >
                                        {providers?.llm.models.map((model) => (
                                            <option key={model.id} value={model.id} className="bg-gray-900">
                                                {model.name}
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
                                        <h3 className="text-lg font-semibold text-white group-hover:text-gray-900 dark:group-hover:text-white">TTS Voice ({voices.filter(v => v.provider === ttsProvider).length} available)</h3>
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
                                                            tts_model: getDefaultTtsModel(providerName),
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

                            {/* Voice Cards Grid - Filtered by Provider */}
                            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 mb-4">
                                {voices
                                    .filter(voice => voice.provider === ttsProvider)
                                    .map((voice) => (
                                        <div
                                            key={voice.id}
                                            onClick={() => setConfig({
                                                ...config,
                                                tts_voice_id: voice.id,
                                                tts_provider: voice.provider,
                                                tts_model: getDefaultTtsModel(voice.provider),
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
                                if (!selectedVoice) return null;
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
