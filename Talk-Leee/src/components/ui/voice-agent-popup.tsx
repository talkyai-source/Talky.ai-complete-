"use client";

import type React from "react";
import { useState, useCallback, useEffect, useRef } from "react";
import { useRouter, usePathname } from "next/navigation";
import { MessageCircle } from "lucide-react";
import { apiBaseUrl } from "@/lib/env";
import { getBrowserAuthToken } from "@/lib/auth-token";

type AIState = "idle" | "connecting" | "browsing" | "listening" | "processing" | "speaking";

interface VoiceAgent {
    id: string;
    name: string;
    gender: string;
    description: string;
}

const VOICE_AGENTS: VoiceAgent[] = [
    { id: "sophia", name: "Sophia", gender: "female", description: "Warm & Professional" },
];

const BAR_COUNT = 14;

const AudioVisualizer: React.FC<{ isActive: boolean; audioLevel: number }> = ({ isActive, audioLevel }) => {
    const barsRef = useRef<(HTMLDivElement | null)[]>([]);
    const rafRef = useRef(0);

    useEffect(() => {
        if (!isActive) return;
        const tick = (t: number) => {
            for (let i = 0; i < BAR_COUNT; i++) {
                const el = barsRef.current[i];
                if (!el) continue;
                const h = Math.max(4, 6 + audioLevel * 14 + Math.sin(t / 90 + i * 0.65) * (3 + audioLevel * 6));
                el.style.height = `${h}px`;
                el.style.opacity = `${0.8 + audioLevel * 0.2}`;
            }
            rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(rafRef.current);
    }, [isActive, audioLevel]);

    if (!isActive) return null;

    return (
        <div className="flex items-center justify-center gap-[3px] h-6">
            {[...Array(BAR_COUNT)].map((_, i) => (
                <div
                    key={i}
                    ref={(el) => { barsRef.current[i] = el; }}
                    className="w-[2px] rounded-full"
                    style={{
                        height: "4px",
                        background: `linear-gradient(to top, #6366f1, #818cf8, #a5b4fc)`,
                        opacity: 0.8,
                    }}
                />
            ))}
        </div>
    );
};

export function VoiceAgentPopup() {
    const router = useRouter();
    const pathname = usePathname();
    const [aiState, setAiState] = useState<AIState>("idle");
    const [popupOpen, setPopupOpen] = useState(false);
    const [audioLevel, setAudioLevel] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [voiceSelected, setVoiceSelected] = useState(false);

    const wsRef = useRef<WebSocket | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const audioQueueRef = useRef<ArrayBuffer[]>([]);
    const micPendingChunksRef = useRef<ArrayBuffer[]>([]);
    const voiceSelectedRef = useRef(false);
    const isPlayingRef = useRef(false);
    const playNextAudioChunkRef = useRef<(() => void) | null>(null);
    const micStreamRef = useRef<MediaStream | null>(null);
    const micAudioContextRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | null>(null);
    const analyserRef = useRef<AnalyserNode | null>(null);
    const outputAnalyserRef = useRef<AnalyserNode | null>(null);
    const levelRafRef = useRef<number | null>(null);

    const selectedVoice = VOICE_AGENTS[0];
    const isActive = popupOpen;

    useEffect(() => {
        voiceSelectedRef.current = voiceSelected;
    }, [voiceSelected]);

    const startLevelLoop = useCallback(() => {
        if (levelRafRef.current) return;
        const micData = new Uint8Array(128);
        const outData = new Uint8Array(128);

        const poll = () => {
            let level = 0;

            const micAnalyser = analyserRef.current;
            if (micAnalyser) {
                micAnalyser.getByteFrequencyData(micData);
                let sum = 0;
                for (let i = 0; i < micData.length; i++) sum += micData[i];
                level = Math.max(level, Math.min(1, sum / micData.length / 128));
            }

            const outAnalyser = outputAnalyserRef.current;
            if (outAnalyser && isPlayingRef.current) {
                outAnalyser.getByteFrequencyData(outData);
                let sum = 0;
                for (let i = 0; i < outData.length; i++) sum += outData[i];
                level = Math.max(level, Math.min(1, sum / outData.length / 100));
            }

            setAudioLevel(level);
            levelRafRef.current = requestAnimationFrame(poll);
        };
        levelRafRef.current = requestAnimationFrame(poll);
    }, []);

    const stopLevelLoop = useCallback(() => {
        if (levelRafRef.current) {
            cancelAnimationFrame(levelRafRef.current);
            levelRafRef.current = null;
        }
    }, []);

    const playNextAudioChunk = useCallback(async () => {
        if (isPlayingRef.current || audioQueueRef.current.length === 0) return;
        isPlayingRef.current = true;

        try {
            if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
                audioContextRef.current = new AudioContext({ sampleRate: 16000 });
            }
            const ctx = audioContextRef.current;
            const buffer = audioQueueRef.current.shift();

            if (buffer) {
                const float32Data = new Float32Array(buffer.byteLength / 4);
                const view = new DataView(buffer);
                for (let i = 0; i < float32Data.length; i++) {
                    float32Data[i] = view.getFloat32(i * 4, true);
                }
                const audioBuffer = ctx.createBuffer(1, float32Data.length, 16000);
                audioBuffer.getChannelData(0).set(float32Data);

                const analyser = ctx.createAnalyser();
                analyser.fftSize = 256;
                outputAnalyserRef.current = analyser;

                const source = ctx.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(analyser);
                analyser.connect(ctx.destination);
                source.start();

                source.onended = () => {
                    isPlayingRef.current = false;
                    outputAnalyserRef.current = null;
                    setAudioLevel(0);
                    playNextAudioChunkRef.current?.();
                };
            } else {
                isPlayingRef.current = false;
            }
        } catch {
            isPlayingRef.current = false;
        }
    }, []);

    useEffect(() => {
        playNextAudioChunkRef.current = () => {
            void playNextAudioChunk();
        };
    }, [playNextAudioChunk]);

    const startMicrophone = useCallback(async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
            });
            micStreamRef.current = stream;

            const audioContext = new AudioContext({ sampleRate: 16000 });
            micAudioContextRef.current = audioContext;
            const source = audioContext.createMediaStreamSource(stream);

            const analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            analyserRef.current = analyser;

            startLevelLoop();

            const processor = audioContext.createScriptProcessor(4096, 1, 1);
            processorRef.current = processor;

            processor.onaudioprocess = (event) => {
                const inputData = event.inputBuffer.getChannelData(0);
                const pcmData = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    const s = Math.max(-1, Math.min(1, inputData[i]));
                    pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                const ws = wsRef.current;
                if (ws && ws.readyState === WebSocket.OPEN && voiceSelectedRef.current) {
                    ws.send(pcmData.buffer);
                    return;
                }

                const bufferCopy = pcmData.buffer.slice(0);
                micPendingChunksRef.current.push(bufferCopy);
                if (micPendingChunksRef.current.length > 60) {
                    micPendingChunksRef.current.shift();
                }
            };

            source.connect(processor);
            processor.connect(audioContext.destination);
        } catch {
            setError("Microphone access denied");
        }
    }, [startLevelLoop]);

    const stopMicrophone = useCallback(() => {
        stopLevelLoop();
        if (processorRef.current) { processorRef.current.disconnect(); processorRef.current = null; }
        if (micAudioContextRef.current) { micAudioContextRef.current.close(); micAudioContextRef.current = null; }
        if (micStreamRef.current) { micStreamRef.current.getTracks().forEach(track => track.stop()); micStreamRef.current = null; }
        analyserRef.current = null;
        outputAnalyserRef.current = null;
        setAudioLevel(0);
    }, [stopLevelLoop]);

    const cleanupWsResources = useCallback(() => {
        const ws = wsRef.current;
        if (ws) {
            try {
                ws.onopen = null;
                ws.onmessage = null;
                ws.onerror = null;
                ws.onclose = null;
            } catch { /* ignore */ }
            try { ws.close(); } catch { /* ignore */ }
            wsRef.current = null;
        }

        if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
        audioQueueRef.current = [];
        isPlayingRef.current = false;
        setVoiceSelected(false);
        voiceSelectedRef.current = false;
    }, []);

    const cleanupSessionResources = useCallback(() => {
        stopMicrophone();
        cleanupWsResources();
        setAudioLevel(0);
        micPendingChunksRef.current = [];
    }, [cleanupWsResources, stopMicrophone]);

    const handleMessage = useCallback(async (event: MessageEvent) => {
        if (event.data instanceof Blob) {
            const arrayBuffer = await event.data.arrayBuffer();
            audioQueueRef.current.push(arrayBuffer);
            setAiState("speaking");
            playNextAudioChunkRef.current?.();
        } else {
            const data = JSON.parse(event.data);
            switch (data.type) {
                case "ready":
                    if (!voiceSelectedRef.current) {
                        voiceSelectedRef.current = true;
                        setVoiceSelected(true);
                    }
                    setAiState("listening");
                    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                        wsRef.current.send(JSON.stringify({ type: "voice_selected", voice_id: selectedVoice.id }));
                        for (const chunk of micPendingChunksRef.current) {
                            wsRef.current.send(chunk);
                        }
                        micPendingChunksRef.current = [];
                    }
                    break;
                case "transcript":
                    if (data.is_final && data.text) setAiState("processing");
                    break;
                case "llm_response":
                    setAiState("speaking");
                    break;
                case "turn_complete":
                    setAiState("listening");
                    break;
                case "barge_in":
                case "tts_interrupted":
                    audioQueueRef.current = [];
                    isPlayingRef.current = false;
                    if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
                    setAiState("listening");
                    break;
                case "error":
                    setError(data.message);
                    break;
            }
        }
    }, [selectedVoice.id]);

    const endSession = useCallback(() => {
        cleanupSessionResources();
        setAiState("idle");
        setPopupOpen(false);
    }, [cleanupSessionResources]);

    const failSession = useCallback((message: string) => {
        setError(message);
        cleanupWsResources();
        setAiState("listening");
    }, [cleanupWsResources]);

    const startSession = useCallback(() => {
        setAiState("listening");
        setError(null);
        setVoiceSelected(false);
        voiceSelectedRef.current = false;
        micPendingChunksRef.current = [];
        void startMicrophone();
        const sessionId = `ask-ai-${Date.now()}`;
        const apiUrl = apiBaseUrl();
        const u = new URL(apiUrl);
        u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
        u.pathname = `${u.pathname.replace(/\/$/, "")}/ws/ai-test/${sessionId}`;
        const ws = new WebSocket(u.toString());
        wsRef.current = ws;

        ws.onopen = () => {
            ws.send(JSON.stringify({ type: "config", voice_id: selectedVoice.id }));
        };
        ws.onmessage = handleMessage;
        ws.onerror = () => { failSession("Connection error"); };
        ws.onclose = () => { failSession("Connection closed"); };
    }, [handleMessage, selectedVoice.id, failSession, startMicrophone]);

    const handleMainButtonClick = useCallback(() => {
        const token = getBrowserAuthToken();
        if (!token) {
            router.push(`/auth/login?next=${encodeURIComponent(pathname)}`);
            return;
        }

        if (!popupOpen) {
            setPopupOpen(true);
            startSession();
        } else {
            endSession();
        }
    }, [popupOpen, startSession, endSession, router, pathname]);

    useEffect(() => {
        return () => {
            cleanupSessionResources();
        };
    }, [cleanupSessionResources]);

    const getStatusText = () => {
        switch (aiState) {
            case "connecting": return "Connecting...";
            case "browsing": return "Tap to select";
            case "listening": return "Listening...";
            case "processing": return "Thinking...";
            case "speaking": return voiceSelected ? "Speaking..." : "Tap to select";
            default: return "Click to talk";
        }
    };

    return (
        <div className="pointer-events-auto fixed bottom-5 right-2 sm:bottom-6 sm:right-3 z-50 flex items-center gap-2">
            <div className="relative">
                <button
                    onClick={handleMainButtonClick}
                    className={`relative rounded-full transition-[background-color,border-color,box-shadow,transform] duration-500 ease-out cursor-pointer group ${isActive ? "overflow-visible" : "overflow-hidden"} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${!isActive
                        ? "stats-card inline-flex items-center justify-center h-10 w-10 px-0 bg-cyan-50/70 border border-cyan-200/80 shadow-[0_25px_50px_-12px_rgba(0,0,0,0.15)] backdrop-blur-sm transition-[background-color,border-color,box-shadow,transform,width,padding] hover:scale-105 md:justify-start md:gap-2 md:px-3 md:w-[150px] dark:bg-cyan-950/60 dark:border-cyan-200/35 dark:shadow-[0_25px_50px_-12px_rgba(0,0,0,0.55),0_0_0_1px_rgba(34,211,238,0.16),0_0_24px_rgba(34,211,238,0.14)]"
                        : "flex items-center justify-center w-20 h-20 lg:w-40 lg:h-40 bg-background/70 border-2 border-indigo-400/40 backdrop-blur-md transition-[width,height]"
                        }`}
                    style={{
                        boxShadow: isActive
                            ? `0 0 40px rgba(99, 102, 241, ${0.2 + audioLevel * 0.2}), 0 0 80px rgba(129, 140, 248, ${0.1 + audioLevel * 0.15})`
                            : undefined,
                    }}
                >
                    {isActive && (
                        <>
                            <div className="absolute -inset-2 rounded-full border-2 border-indigo-400/25 heroAskAiPing" />
                            <div className="absolute -inset-2 rounded-full border-2 border-indigo-400/20 heroAskAiPing" style={{ animationDelay: "300ms" }} />
                            <div className="absolute -inset-2 rounded-full border-2 border-indigo-400/15 heroAskAiPing" style={{ animationDelay: "600ms" }} />
                        </>
                    )}

                    {!isActive ? (
                        <div className="relative z-10 flex items-center gap-2">
                            <span className="relative inline-flex h-7 w-7 items-center justify-center rounded-lg bg-cyan-500 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.35)]">
                                <MessageCircle className="h-4 w-4 text-white" />
                                <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-white/90" />
                            </span>
                            <div className="hidden md:flex flex-col items-start leading-tight">
                                <h3 className="text-sm font-semibold leading-none text-primary dark:text-white">Ask AI</h3>
                                <p className="text-[10px] leading-none text-primary/80 dark:text-white/80">{getStatusText()}</p>
                            </div>
                        </div>
                    ) : (
                        <div className="relative z-10 flex items-center justify-center">
                            <AudioVisualizer isActive={true} audioLevel={audioLevel} />
                        </div>
                    )}
                </button>
            </div>
            {error && <p className="absolute -bottom-10 left-1/2 -translate-x-1/2 text-xs text-red-500 whitespace-nowrap">{error}</p>}
        </div>
    );
}
