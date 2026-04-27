"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import {
    aiOptionsApi,
    AIProviderConfig,
    ProviderListResponse,
    VoiceInfo,
    DEFAULT_CONFIG
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

interface DummyCallMessage {
    role: "user" | "assistant" | "system";
    content: string;
    timestamp: number;
}

interface DummyCallState {
    isActive: boolean;
    sessionId: string | null;
    callId: string | null;
    conversationState: string;
    agentName: string;
    companyName: string;
    messages: DummyCallMessage[];
    latency: {
        llm_ms?: number;
        tts_ms?: number;
    };
}

export default function AIOptionsPage() {
    // State
    const [providers, setProviders] = useState<ProviderListResponse | null>(null);
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [config, setConfig] = useState<AIProviderConfig>(DEFAULT_CONFIG);
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
    const [ttsProvider, setTtsProvider] = useState<"cartesia" | "google">("cartesia");

    // Dummy Call state
    const [dummyCall, setDummyCall] = useState<DummyCallState>({
        isActive: false,
        sessionId: null,
        callId: null,
        conversationState: "greeting",
        agentName: "Alex",
        companyName: "Your Company",
        messages: [],
        latency: {}
    });

    const [dummyCallConnecting, setDummyCallConnecting] = useState(false);

    // WebSocket ref
    const wsRef = useRef<WebSocket | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const audioQueueRef = useRef<ArrayBuffer[]>([]);
    const isPlayingRef = useRef(false);

    // Microphone capture refs
    const micStreamRef = useRef<MediaStream | null>(null);
    const micAudioContextRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);

    useEffect(() => {
        loadData();
    }, []);

    useEffect(() => {
        const models = providers?.llm.models ?? [];
        if (models.length === 0) return;
        const selected = config.llm_model;
        if (typeof selected === "string" && models.some((m) => m.id === selected)) return;
        setConfig((prev) => ({ ...prev, llm_model: models[0]!.id }));
    }, [config.llm_model, providers?.llm.models]);

    async function loadData() {
        try {
            setLoading(true);
            setError("");

            const [providersData, voicesData, configData] = await Promise.all([
                aiOptionsApi.getProviders(),
                aiOptionsApi.getVoices().catch(() => []), // Voices may fail if no API key
                aiOptionsApi.getConfig().catch(() => DEFAULT_CONFIG),
            ]);

            setProviders(providersData);
            setVoices(voicesData);
            setConfig(configData);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load AI options");
        } finally {
            setLoading(false);
        }
    }

    async function handleSaveConfig() {
        try {
            await aiOptionsApi.saveConfig(config);
            setSaveSuccess(true);
            setTimeout(() => setSaveSuccess(false), 3000);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to save configuration");
        }
    }

    async function handleTestLLM() {
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

            // Determine sample rate based on voice ID (Google Chirp 3 HD uses 24kHz, Cartesia uses 16kHz)
            const isGoogleVoice = voiceId.includes("Chirp3-HD");
            const sampleRate = isGoogleVoice ? 24000 : 16000;

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

    // Dummy Call - WebSocket connection and handlers
    function startDummyCall() {
        if (wsRef.current) return;

        setDummyCallConnecting(true);
        setError("");

        const sessionId = `session-${Date.now()}`;
        const apiUrl = apiBaseUrl();
        const u = new URL(apiUrl);
        u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
        u.pathname = `${u.pathname.replace(/\/$/, "")}/ws/ai-test/${sessionId}`;
        const wsUrl = u.toString();

        try {
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                // Send config message to start session
                ws.send(JSON.stringify({
                    type: "config",
                    config: config
                }));
            };

            ws.onmessage = async (event) => {
                if (event.data instanceof Blob) {
                    // Binary audio data - queue for playback
                    const arrayBuffer = await event.data.arrayBuffer();
                    audioQueueRef.current.push(arrayBuffer);
                    playNextAudioChunk();
                } else {
                    // JSON message
                    const data = JSON.parse(event.data);
                    handleDummyCallMessage(data);
                }
            };

            ws.onerror = (err) => {
                captureException(err, { area: "ai-options", kind: "websocket" });
                setError("Connection error");
                endDummyCall();
            };

            ws.onclose = () => {
                endDummyCall();
            };
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to connect");
            setDummyCallConnecting(false);
        }
    }

    const handleDummyCallMessage = (data: unknown) => {
        if (!data || typeof data !== "object") return;
        const payload = data as Record<string, unknown>;
        const type = typeof payload.type === "string" ? payload.type : "";
        switch (type) {
            case "ready":
                setDummyCall(prev => ({
                    ...prev,
                    isActive: true,
                    sessionId: typeof payload.session_id === "string" ? payload.session_id : prev.sessionId,
                    callId: typeof payload.call_id === "string" ? payload.call_id : prev.callId,
                    conversationState: typeof payload.state === "string" ? payload.state : "greeting",
                    agentName: typeof payload.agent_name === "string" ? payload.agent_name : "Alex",
                    companyName: typeof payload.company_name === "string" ? payload.company_name : "Your Company"
                }));
                setDummyCallConnecting(false);
                // Auto-start microphone capture when call connects (like real phone call)
                startMicrophone();
                break;

            case "transcript":
                // User speech transcribed (voice mode) - add user message when final
                if (payload.is_final === true && typeof payload.text === "string" && payload.text.trim()) {
                    setDummyCall(prev => ({
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
                setDummyCall(prev => ({
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
                setDummyCall(prev => ({
                    ...prev,
                    conversationState: typeof payload.state === "string" ? payload.state : prev.conversationState
                }));
                break;

            case "turn_complete":
                setDummyCall(prev => ({
                    ...prev,
                    latency: {
                        llm_ms: typeof payload.llm_latency_ms === "number" ? payload.llm_latency_ms : prev.latency.llm_ms,
                        tts_ms: typeof payload.tts_latency_ms === "number" ? payload.tts_latency_ms : prev.latency.tts_ms
                    }
                }));
                break;

            case "barge_in":
                // User started speaking - stop TTS playback immediately
                // Clear audio queue to stop any pending audio
                audioQueueRef.current = [];
                isPlayingRef.current = false;
                // Close and recreate audio context to stop current playback
                if (audioContextRef.current) {
                    audioContextRef.current.close();
                    audioContextRef.current = null;
                }
                break;

            case "tts_interrupted":
                // TTS was interrupted due to barge-in
                // Clear audio queue
                audioQueueRef.current = [];
                isPlayingRef.current = false;
                break;

            case "error":
                setError(typeof payload.message === "string" ? payload.message : "Unknown error");
                break;
        }
    };

    const playNextAudioChunk = async () => {
        if (isPlayingRef.current || audioQueueRef.current.length === 0) return;

        isPlayingRef.current = true;

        try {
            const sampleRate = config.tts_sample_rate || 16000;

            if (!audioContextRef.current) {
                audioContextRef.current = new AudioContext({ sampleRate });
            }

            const ctx = audioContextRef.current;
            const buffer = audioQueueRef.current.shift();

            if (buffer) {
                // Convert PCM Float32 to AudioBuffer
                const float32Data = new Float32Array(buffer.byteLength / 4);
                const view = new DataView(buffer);
                for (let i = 0; i < float32Data.length; i++) {
                    float32Data[i] = view.getFloat32(i * 4, true);
                }

                const audioBuffer = ctx.createBuffer(1, float32Data.length, sampleRate);
                audioBuffer.getChannelData(0).set(float32Data);

                const source = ctx.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(ctx.destination);
                source.start();

                source.onended = () => {
                    isPlayingRef.current = false;
                    playNextAudioChunk();
                };
            } else {
                isPlayingRef.current = false;
            }
        } catch (err) {
            captureException(err, { area: "ai-options", kind: "audio-playback" });
            isPlayingRef.current = false;
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

            processor.onaudioprocess = (event) => {
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
            processor.connect(audioContext.destination);
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

        if (micAudioContextRef.current) {
            micAudioContextRef.current.close();
            micAudioContextRef.current = null;
        }

        if (micStreamRef.current) {
            micStreamRef.current.getTracks().forEach(track => track.stop());
            micStreamRef.current = null;
        }
    };

    const endDummyCall = useCallback(() => {
        // Stop microphone capture
        stopMicrophone();

        if (wsRef.current) {
            wsRef.current.send(JSON.stringify({ type: "end_call" }));
            wsRef.current.close();
            wsRef.current = null;
        }

        if (audioContextRef.current) {
            audioContextRef.current.close();
            audioContextRef.current = null;
        }

        audioQueueRef.current = [];
        isPlayingRef.current = false;

        setDummyCall({
            isActive: false,
            sessionId: null,
            callId: null,
            conversationState: "greeting",
            agentName: "Alex",
            companyName: "Your Company",
            messages: [],
            latency: {}
        });
        setDummyCallConnecting(false);
    }, []);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
            if (audioContextRef.current) {
                audioContextRef.current.close();
            }
        };
    }, []);

    return (
        <DashboardLayout title="AI Options" description="Configure LLM, STT, and TTS providers">
            {loading ? (
                <div className="flex items-center justify-center h-64">
                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white" />
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

                    {/* Dummy Call - Test Full Pipeline */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="content-card group border-2 border-orange-500/30 bg-gradient-to-br from-orange-500/5 to-transparent"
                    >
                        <div className="flex items-center justify-between mb-6">
                            <div className="flex items-center gap-3">
                                <div className="p-3 bg-emerald-500/25 dark:bg-white/10 rounded-lg">
                                    <Phone className="w-6 h-6 text-gray-900 dark:text-white" />
                                </div>
                                <div>
                                    <h3 className="text-xl font-bold text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white">Dummy Call</h3>
                                    <p className="text-sm text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">
                                        Test the <span className="font-medium">exact same pipeline</span> used for real calls
                                    </p>
                                </div>
                            </div>

                            {!dummyCall.isActive ? (
                                <button
                                    onClick={startDummyCall}
                                    disabled={dummyCallConnecting}
                                    className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-teal-500 to-teal-600 hover:from-teal-600 hover:to-teal-700 rounded-lg text-white font-medium transition-all shadow-lg shadow-teal-500/25 hover:scale-[1.02] active:scale-[0.99] disabled:opacity-50"
                                >
                                    {dummyCallConnecting ? (
                                        <RefreshCw className="w-5 h-5 animate-spin" />
                                    ) : (
                                        <Phone className="w-5 h-5" />
                                    )}
                                    <span>{dummyCallConnecting ? "Connecting..." : "Start Dummy Call"}</span>
                                </button>
                            ) : (
                                <button
                                    onClick={endDummyCall}
                                    className="flex items-center gap-2 px-6 py-3 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 rounded-lg text-red-400 hover:text-white font-medium transition-all hover:scale-[1.02] active:scale-[0.99]"
                                >
                                    <PhoneOff className="w-5 h-5" />
                                    <span>End Call</span>
                                </button>
                            )}
                        </div>

                        {dummyCall.isActive && (
                            <div className="space-y-4">
                                {/* Network Latency Bar - Always visible above the call */}
                                <div className="flex items-center justify-center gap-6 p-3 bg-gradient-to-r from-purple-500/10 via-emerald-500/10 to-blue-500/10 rounded-lg border border-white/10 group-hover:border-black/10 dark:group-hover:border-white/10">
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-purple-500 rounded-full" />
                                        <span className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">LLM:</span>
                                        <span className="text-sm font-mono font-bold text-purple-400">
                                            {dummyCall.latency.llm_ms ? `${dummyCall.latency.llm_ms.toFixed(0)}ms` : '--'}
                                        </span>
                                    </div>
                                    <div className="h-4 w-px bg-white/20 group-hover:bg-black/10 dark:group-hover:bg-white/20" />
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-emerald-500 rounded-full" />
                                        <span className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">TTS:</span>
                                        <span className="text-sm font-mono font-bold text-emerald-400">
                                            {dummyCall.latency.tts_ms ? `${dummyCall.latency.tts_ms.toFixed(0)}ms` : '--'}
                                        </span>
                                    </div>
                                    <div className="h-4 w-px bg-white/20 group-hover:bg-black/10 dark:group-hover:bg-white/20" />
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-yellow-500 rounded-full" />
                                        <span className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">Total:</span>
                                        <span className="text-sm font-mono font-bold text-yellow-400">
                                            {(dummyCall.latency.llm_ms && dummyCall.latency.tts_ms)
                                                ? `${(dummyCall.latency.llm_ms + dummyCall.latency.tts_ms).toFixed(0)}ms`
                                                : '--'}
                                        </span>
                                    </div>
                                </div>

                                {/* Call Info Bar */}
                                <div className="flex items-center justify-between p-3 bg-orange-500/10 rounded-lg border border-orange-500/20">
                                    <div className="flex items-center gap-4">
                                        <div className="flex items-center gap-2">
                                            <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                                            <span className="text-sm text-gray-700 dark:text-gray-300 group-hover:text-gray-900 dark:group-hover:text-gray-300">
                                                <span className="text-orange-400">{dummyCall.agentName}</span> from{" "}
                                                <span className="text-orange-400">{dummyCall.companyName}</span>
                                            </span>
                                        </div>
                                        <div className="h-4 w-px bg-white/20 group-hover:bg-black/10 dark:group-hover:bg-white/20" />
                                        <span className="text-xs px-2 py-1 bg-orange-500/20 rounded text-orange-400 uppercase font-medium">
                                            {dummyCall.conversationState}
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2 text-xs">
                                        <span className="px-2 py-1 bg-blue-500/20 rounded text-blue-400">
                                            Provider: {config.tts_provider || 'cartesia'}
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
                                    {dummyCall.messages.length === 0 ? (
                                        <div className="flex flex-col items-center justify-center h-full text-gray-700 dark:text-gray-500 group-hover:text-gray-800 dark:group-hover:text-gray-500 gap-2">
                                            <Mic className="w-8 h-8 text-gray-700 dark:text-gray-500 group-hover:text-gray-800 dark:group-hover:text-gray-500" />
                                            <p>Listening... Speak into your microphone</p>
                                            <p className="text-xs text-gray-700 dark:text-gray-600 group-hover:text-gray-800 dark:group-hover:text-gray-600">Your speech will be transcribed automatically</p>
                                        </div>
                                    ) : (
                                        dummyCall.messages.map((msg, idx) => (
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
                                                        ? "bg-blue-500/20 border border-blue-500/30 text-blue-900 dark:text-blue-100 group-hover:bg-blue-500/10 group-hover:border-blue-500/20 group-hover:text-blue-900 dark:group-hover:text-blue-100"
                                                        : "bg-white/5 border border-white/10 text-gray-900 dark:text-gray-200 group-hover:bg-black/5 group-hover:border-black/10 group-hover:text-gray-800 dark:group-hover:bg-white/5 dark:group-hover:border-white/10 dark:group-hover:text-gray-200 dark:group-hover:hover:bg-white/10"
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

                                <p className="text-xs text-gray-700 dark:text-gray-500 group-hover:text-gray-800 dark:group-hover:text-gray-500 text-center">
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
                                <div className="p-2 bg-emerald-500/25 dark:bg-white/10 rounded-lg">
                                    <Cpu className="w-5 h-5 text-gray-900 dark:text-white" />
                                </div>
                                <div>
                                    <h3 className="text-lg font-semibold text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white">LLM Model</h3>
                                    <p className="text-sm text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">Groq AI</p>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div>
                                    {(() => {
                                        const llmModels = providers?.llm.models ?? [];
                                        const hasModels = llmModels.length > 0;
                                        return (
                                            <>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mb-2">Model</label>
                                    <select
                                        value={hasModels ? config.llm_model : ""}
                                        onChange={(e) => setConfig({ ...config, llm_model: e.target.value })}
                                        disabled={!hasModels}
                                        className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-gray-900 dark:text-white group-hover:text-gray-900 group-hover:bg-black/5 group-hover:border-black/10 dark:group-hover:text-white dark:group-hover:bg-white/5 dark:group-hover:border-white/10 focus:outline-none focus:border-purple-500/50"
                                    >
                                        {hasModels ? (
                                            llmModels.map((model) => (
                                                <option key={model.id} value={model.id}>
                                                    {model.name}
                                                </option>
                                            ))
                                        ) : (
                                            <option value="" disabled>
                                                No models available
                                            </option>
                                        )}
                                    </select>
                                    {!hasModels ? (
                                        <p className="mt-2 text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">
                                            No LLM models were returned from the API.
                                        </p>
                                    ) : null}
                                            </>
                                        );
                                    })()}
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mb-2">
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
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mb-2">
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
                                    <div className="p-2 bg-emerald-500/25 dark:bg-white/10 rounded-lg">
                                        <Volume2 className="w-5 h-5 text-gray-900 dark:text-white" />
                                    </div>
                                    <div>
                                        <h3 className="text-lg font-semibold text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white">TTS Voice ({voices.filter(v => v.provider === ttsProvider).length} available)</h3>
                                        <p className="text-sm text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">Select a voice for your AI agent</p>
                                    </div>
                                </div>

                                {/* Provider Selector */}
                                <div className="flex gap-2 p-1 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg border border-white/10 group-hover:border-black/10 dark:group-hover:border-white/10">
                                    <button
                                        onClick={() => setTtsProvider("cartesia")}
                                        className="px-4 py-2 rounded-md text-sm font-medium transition-all bg-gradient-to-r from-teal-500 to-teal-600 hover:from-teal-600 hover:to-teal-700 text-white shadow-lg shadow-teal-500/25 hover:scale-[1.02] active:scale-[0.99]"
                                    >
                                        Cartesia ({voices.filter(v => v.provider === "cartesia").length})
                                    </button>
                                    <button
                                        onClick={() => setTtsProvider("google")}
                                        className="px-4 py-2 rounded-md text-sm font-medium transition-all bg-gradient-to-r from-teal-500 to-teal-600 hover:from-teal-600 hover:to-teal-700 text-white shadow-lg shadow-teal-500/25 hover:scale-[1.02] active:scale-[0.99]"
                                    >
                                        Google ({voices.filter(v => v.provider === "google").length})
                                    </button>
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
                                                tts_provider: voice.provider === 'google' ? 'google' : 'cartesia',
                                                tts_sample_rate: voice.provider === 'google' ? 24000 : 16000
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
                                                    <p className="font-medium text-sm text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white">{voice.name}</p>
                                                </div>
                                                <p className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mt-1 line-clamp-2">
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
                                            <p className="text-sm font-medium text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white">{selectedVoice.name}</p>
                                            <p className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">{selectedVoice.description}</p>
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
                                <div className="p-2 bg-emerald-500/25 dark:bg-white/10 rounded-lg">
                                    <Zap className="w-5 h-5 text-gray-900 dark:text-white" />
                                </div>
                                <div>
                                    <h3 className="text-lg font-semibold text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white">Latency Metrics</h3>
                                    <p className="text-sm text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">Real-time performance tracking</p>
                                </div>
                            </div>
                            <button
                                onClick={handleRunBenchmark}
                                disabled={benchmarking}
                                className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-teal-500 to-teal-600 hover:from-teal-600 hover:to-teal-700 rounded-lg text-white font-medium transition-all shadow-lg shadow-teal-500/25 hover:scale-[1.02] active:scale-[0.99] disabled:opacity-50"
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
                                <p className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mt-1">LLM First Token (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-purple-400">
                                    {latencyMetrics.llm_total_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mt-1">LLM Total (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-emerald-400">
                                    {latencyMetrics.tts_first_audio_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mt-1">TTS First Audio (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-emerald-400">
                                    {latencyMetrics.tts_total_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mt-1">TTS Total (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 group-hover:bg-black/5 dark:group-hover:bg-white/5 rounded-lg text-center transition-[transform,background-color,box-shadow] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:scale-[1.02] hover:shadow-md">
                                <p className="text-2xl font-bold text-yellow-400">
                                    {latencyMetrics.total_pipeline_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400 mt-1">Total Pipeline (ms)</p>
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
                            <div className="p-2 bg-emerald-500/25 dark:bg-white/10 rounded-lg">
                                <MessageSquare className="w-5 h-5 text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white" />
                            </div>
                            <div>
                                <h3 className="text-lg font-semibold text-gray-900 dark:text-white group-hover:text-gray-900 dark:group-hover:text-white">Test LLM</h3>
                                <p className="text-sm text-gray-700 dark:text-gray-400 group-hover:text-gray-900 dark:group-hover:text-gray-400">Send a message to test the selected model</p>
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
                                    className="flex-1 bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-gray-900 dark:text-white group-hover:text-gray-900 group-hover:bg-black/5 group-hover:border-black/10 dark:group-hover:text-white dark:group-hover:bg-white/5 dark:group-hover:border-white/10 placeholder-gray-500 transition-[background-color,border-color,color] duration-150 ease-out hover:bg-white/10 group-hover:hover:bg-black/10 dark:group-hover:hover:bg-white/10 hover:border-white/20 focus:outline-none focus:border-purple-500/50"
                                />
                                <button
                                    onClick={handleTestLLM}
                                    disabled={testing || !testMessage.trim()}
                                    className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-teal-500 to-teal-600 hover:from-teal-600 hover:to-teal-700 rounded-lg text-white font-medium transition-all shadow-lg shadow-teal-500/25 hover:scale-[1.02] active:scale-[0.99]"
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
                                    <p className="text-sm text-gray-900 dark:text-gray-300 whitespace-pre-wrap">{testResponse}</p>
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
                            className="flex items-center gap-2 px-8 py-3 bg-gradient-to-r from-teal-500 to-teal-600 hover:from-teal-600 hover:to-teal-700 rounded-lg text-white font-medium transition-all shadow-lg shadow-teal-500/25 hover:scale-[1.02] active:scale-[0.99]"
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
