"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import {
    aiOptionsApi,
    AIProviderConfig,
    ProviderListResponse,
    VoiceInfo,
    LLMTestResponse,
    TTSTestResponse,
    LatencyBenchmarkResponse,
    DEFAULT_CONFIG
} from "@/lib/ai-options-api";
import {
    Cpu,
    Mic,
    Volume2,
    Settings,
    Zap,
    Play,
    Send,
    RefreshCw,
    Check,
    AlertCircle,
    Clock,
    MessageSquare,
    ChevronDown,
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

    // Voice preview state
    const [previewText, setPreviewText] = useState("Hello, I am your AI voice assistant. How can I help you today?");
    const [previewing, setPreviewing] = useState(false);
    const [previewingVoiceId, setPreviewingVoiceId] = useState<string | null>(null);

    // TTS Provider filter state - Google only (Cartesia disabled)
    const [ttsProvider, setTtsProvider] = useState<"google">("google");

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

    async function handlePreviewVoice() {
        try {
            setPreviewing(true);
            setError("");

            const response = await aiOptionsApi.testTTS({
                model: config.tts_model,
                voice_id: config.tts_voice_id,
                text: previewText,
                sample_rate: config.tts_sample_rate,
            });

            setLatencyMetrics(prev => ({
                ...prev,
                tts_first_audio_ms: response.first_audio_ms,
                tts_total_ms: response.latency_ms,
            }));

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

            const audioContext = new AudioContext({ sampleRate: config.tts_sample_rate });
            const audioBuffer = audioContext.createBuffer(1, audioArray.length, config.tts_sample_rate);
            audioBuffer.getChannelData(0).set(audioArray);

            const source = audioContext.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(audioContext.destination);
            source.start();

            source.onended = () => {
                audioContext.close();
            };
        } catch (err) {
            setError(err instanceof Error ? err.message : "Voice preview failed");
        } finally {
            setPreviewing(false);
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
    const startDummyCall = useCallback(() => {
        if (wsRef.current) return;

        setDummyCallConnecting(true);
        setError("");

        const sessionId = `session-${Date.now()}`;
        const wsUrl = `ws://localhost:8000/api/v1/ws/ai-test/${sessionId}`;

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
                console.error("WebSocket error:", err);
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
    }, [config]);

    const handleDummyCallMessage = (data: any) => {
        switch (data.type) {
            case "ready":
                setDummyCall(prev => ({
                    ...prev,
                    isActive: true,
                    sessionId: data.session_id,
                    callId: data.call_id,
                    conversationState: data.state || "greeting",
                    agentName: data.agent_name || "Alex",
                    companyName: data.company_name || "Your Company"
                }));
                setDummyCallConnecting(false);
                // Auto-start microphone capture when call connects (like real phone call)
                startMicrophone();
                break;

            case "transcript":
                // User speech transcribed (voice mode) - add user message when final
                if (data.is_final && data.text && data.text.trim()) {
                    setDummyCall(prev => ({
                        ...prev,
                        messages: [...prev.messages, {
                            role: "user",
                            content: data.text,
                            timestamp: Date.now()
                        }]
                    }));
                }
                break;

            case "llm_response":
                setDummyCall(prev => ({
                    ...prev,
                    messages: [...prev.messages, {
                        role: "assistant",
                        content: data.text,
                        timestamp: Date.now()
                    }],
                    latency: {
                        ...prev.latency,
                        llm_ms: data.latency_ms
                    }
                }));
                break;

            case "state_change":
                setDummyCall(prev => ({
                    ...prev,
                    conversationState: data.state
                }));
                break;

            case "turn_complete":
                setDummyCall(prev => ({
                    ...prev,
                    latency: {
                        llm_ms: data.llm_latency_ms,
                        tts_ms: data.tts_latency_ms
                    }
                }));
                break;

            case "barge_in":
                // User started speaking - stop TTS playback immediately
                console.log("Barge-in detected: stopping audio playback");
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
                console.log("TTS interrupted:", data.reason);
                // Clear audio queue
                audioQueueRef.current = [];
                isPlayingRef.current = false;
                break;

            case "error":
                setError(data.message);
                break;
        }
    };

    const playNextAudioChunk = async () => {
        if (isPlayingRef.current || audioQueueRef.current.length === 0) return;

        isPlayingRef.current = true;

        try {
            const sampleRate = config.tts_sample_rate || 24000;  // Official Cartesia: 24kHz

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
            console.error("Audio playback error:", err);
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

            console.log("Microphone started - streaming to backend");
        } catch (err) {
            console.error("Microphone error:", err);
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

        console.log("Microphone stopped");
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

    // Find current voice name
    const currentVoiceName = voices.find(v => v.id === config.tts_voice_id)?.name || "Default Voice";

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
                        className="content-card border-2 border-orange-500/30 bg-gradient-to-br from-orange-500/5 to-transparent"
                    >
                        <div className="flex items-center justify-between mb-6">
                            <div className="flex items-center gap-3">
                                <div className="p-3 bg-orange-500/20 rounded-lg">
                                    <Phone className="w-6 h-6 text-orange-400" />
                                </div>
                                <div>
                                    <h3 className="text-xl font-bold text-white">Dummy Call</h3>
                                    <p className="text-sm text-gray-400">
                                        Test the <span className="text-orange-400 font-medium">exact same pipeline</span> used for real calls
                                    </p>
                                </div>
                            </div>

                            {!dummyCall.isActive ? (
                                <button
                                    onClick={startDummyCall}
                                    disabled={dummyCallConnecting}
                                    className="flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-600 hover:to-red-600 rounded-lg text-white font-medium transition-all shadow-lg shadow-orange-500/25 disabled:opacity-50"
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
                                    className="flex items-center gap-2 px-6 py-3 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 rounded-lg text-red-400 font-medium transition-all"
                                >
                                    <PhoneOff className="w-5 h-5" />
                                    <span>End Call</span>
                                </button>
                            )}
                        </div>

                        {dummyCall.isActive && (
                            <div className="space-y-4">
                                {/* Network Latency Bar - Always visible above the call */}
                                <div className="flex items-center justify-center gap-6 p-3 bg-gradient-to-r from-purple-500/10 via-emerald-500/10 to-blue-500/10 rounded-lg border border-white/10">
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-purple-500 rounded-full" />
                                        <span className="text-xs text-gray-400">LLM:</span>
                                        <span className="text-sm font-mono font-bold text-purple-400">
                                            {dummyCall.latency.llm_ms ? `${dummyCall.latency.llm_ms.toFixed(0)}ms` : '--'}
                                        </span>
                                    </div>
                                    <div className="h-4 w-px bg-white/20" />
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-emerald-500 rounded-full" />
                                        <span className="text-xs text-gray-400">TTS:</span>
                                        <span className="text-sm font-mono font-bold text-emerald-400">
                                            {dummyCall.latency.tts_ms ? `${dummyCall.latency.tts_ms.toFixed(0)}ms` : '--'}
                                        </span>
                                    </div>
                                    <div className="h-4 w-px bg-white/20" />
                                    <div className="flex items-center gap-2">
                                        <div className="w-2 h-2 bg-yellow-500 rounded-full" />
                                        <span className="text-xs text-gray-400">Total:</span>
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
                                            <span className="text-sm text-gray-300">
                                                <span className="text-orange-400">{dummyCall.agentName}</span> from{" "}
                                                <span className="text-orange-400">{dummyCall.companyName}</span>
                                            </span>
                                        </div>
                                        <div className="h-4 w-px bg-white/20" />
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
                                <div className="h-64 overflow-y-auto p-4 bg-black/20 rounded-lg border border-white/5 space-y-3">
                                    {dummyCall.messages.length === 0 ? (
                                        <div className="flex flex-col items-center justify-center h-full text-gray-500 gap-2">
                                            <Mic className="w-8 h-8 text-gray-600" />
                                            <p>Listening... Speak into your microphone</p>
                                            <p className="text-xs text-gray-600">Your speech will be transcribed automatically</p>
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
                                                        ? "bg-blue-500/20 border border-blue-500/30 text-blue-100"
                                                        : "bg-white/5 border border-white/10 text-gray-200"
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

                                <p className="text-xs text-gray-500 text-center">
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
                            className="content-card"
                        >
                            <div className="flex items-center gap-3 mb-6">
                                <div className="p-2 bg-purple-500/20 rounded-lg">
                                    <Cpu className="w-5 h-5 text-purple-400" />
                                </div>
                                <div>
                                    <h3 className="text-lg font-semibold text-white">LLM Model</h3>
                                    <p className="text-sm text-gray-400">Groq AI</p>
                                </div>
                            </div>

                            <div className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-400 mb-2">Model</label>
                                    <select
                                        value={config.llm_model}
                                        onChange={(e) => setConfig({ ...config, llm_model: e.target.value })}
                                        className="w-full bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-purple-500/50"
                                    >
                                        {providers?.llm.models.map((model) => (
                                            <option key={model.id} value={model.id} className="bg-gray-900">
                                                {model.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-400 mb-2">
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
                                    <label className="block text-sm font-medium text-gray-400 mb-2">
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
                            className="content-card md:col-span-2"
                        >
                            <div className="flex items-center justify-between mb-6">
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-emerald-500/20 rounded-lg">
                                        <Volume2 className="w-5 h-5 text-emerald-400" />
                                    </div>
                                    <div>
                                        <h3 className="text-lg font-semibold text-white">TTS Voice ({voices.filter(v => v.provider === ttsProvider).length} available)</h3>
                                        <p className="text-sm text-gray-400">Select a voice for your AI agent</p>
                                    </div>
                                </div>

                                {/* Provider Info - Google Only (Cartesia disabled) */}
                                <div className="flex gap-2 p-1 bg-white/5 rounded-lg border border-white/10">
                                    <div className="px-4 py-2 rounded-md text-sm font-medium bg-blue-500 text-white shadow-lg shadow-blue-500/25">
                                        Google Chirp3-HD ({voices.filter(v => v.provider === "google").length} voices)
                                    </div>
                                    <div className="px-4 py-2 rounded-md text-xs text-gray-500 flex items-center">
                                        Cartesia disabled
                                    </div>
                                </div>
                            </div>

                            {/* Voice Cards Grid - Google voices only */}
                            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 mb-4">
                                {voices
                                    .filter(voice => voice.provider === "google")
                                    .map((voice) => (
                                        <div
                                            key={voice.id}
                                            onClick={() => setConfig({
                                                ...config,
                                                tts_voice_id: voice.id,
                                                tts_provider: 'google',
                                                tts_sample_rate: 24000  // Google Chirp3-HD sample rate
                                            })}
                                            className={`relative p-3 rounded-lg border cursor-pointer transition-all hover:scale-[1.02] ${config.tts_voice_id === voice.id
                                                ? "border-emerald-500 bg-emerald-500/20"
                                                : "border-white/20 bg-white/5 hover:bg-white/10"
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
                                                    <p className="font-medium text-sm text-white">{voice.name}</p>
                                                </div>
                                                <p className="text-xs text-gray-400 mt-1 line-clamp-2">
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
                                                <p className="text-sm font-medium text-white">{selectedVoice.name}</p>
                                                <p className="text-xs text-gray-400">{selectedVoice.description}</p>
                                            </div>
                                            <button
                                                onClick={() => handlePreviewVoiceById(selectedVoice.id)}
                                                disabled={previewingVoiceId === selectedVoice.id}
                                                className="px-4 py-2 bg-emerald-500/30 hover:bg-emerald-500/40 rounded-lg text-emerald-400 text-sm flex items-center gap-2 transition-colors"
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
                        className="content-card"
                    >
                        <div className="flex items-center justify-between mb-6">
                            <div className="flex items-center gap-3">
                                <div className="p-2 bg-yellow-500/20 rounded-lg">
                                    <Zap className="w-5 h-5 text-yellow-400" />
                                </div>
                                <div>
                                    <h3 className="text-lg font-semibold text-white">Latency Metrics</h3>
                                    <p className="text-sm text-gray-400">Real-time performance tracking</p>
                                </div>
                            </div>
                            <button
                                onClick={handleRunBenchmark}
                                disabled={benchmarking}
                                className="flex items-center gap-2 px-4 py-2 bg-yellow-500/20 hover:bg-yellow-500/30 border border-yellow-500/30 rounded-lg text-yellow-400 transition-colors disabled:opacity-50"
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
                            <div className="p-4 bg-white/5 rounded-lg text-center">
                                <p className="text-2xl font-bold text-purple-400">
                                    {latencyMetrics.llm_first_token_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 mt-1">LLM First Token (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 rounded-lg text-center">
                                <p className="text-2xl font-bold text-purple-400">
                                    {latencyMetrics.llm_total_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 mt-1">LLM Total (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 rounded-lg text-center">
                                <p className="text-2xl font-bold text-emerald-400">
                                    {latencyMetrics.tts_first_audio_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 mt-1">TTS First Audio (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 rounded-lg text-center">
                                <p className="text-2xl font-bold text-emerald-400">
                                    {latencyMetrics.tts_total_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 mt-1">TTS Total (ms)</p>
                            </div>
                            <div className="p-4 bg-white/5 rounded-lg text-center">
                                <p className="text-2xl font-bold text-yellow-400">
                                    {latencyMetrics.total_pipeline_ms?.toFixed(0) || "—"}
                                </p>
                                <p className="text-xs text-gray-400 mt-1">Total Pipeline (ms)</p>
                            </div>
                        </div>
                    </motion.div>

                    {/* LLM Test Section */}
                    <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.5 }}
                        className="content-card"
                    >
                        <div className="flex items-center gap-3 mb-6">
                            <div className="p-2 bg-white/10 rounded-lg">
                                <MessageSquare className="w-5 h-5 text-white" />
                            </div>
                            <div>
                                <h3 className="text-lg font-semibold text-white">Test LLM</h3>
                                <p className="text-sm text-gray-400">Send a message to test the selected model</p>
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
                                    className="flex-1 bg-white/5 border border-white/10 rounded-lg px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:border-purple-500/50"
                                />
                                <button
                                    onClick={handleTestLLM}
                                    disabled={testing || !testMessage.trim()}
                                    className="flex items-center gap-2 px-6 py-3 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 rounded-lg text-purple-400 transition-colors disabled:opacity-50"
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
                            className="flex items-center gap-2 px-8 py-3 bg-gradient-to-r from-purple-500 to-blue-500 hover:from-purple-600 hover:to-blue-600 rounded-lg text-white font-medium transition-all shadow-lg shadow-purple-500/25"
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
