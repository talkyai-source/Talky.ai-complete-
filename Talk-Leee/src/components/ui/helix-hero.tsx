"use client";

import type React from "react";
import { useMemo, useRef, useState, useCallback, useEffect } from "react";
import { MagneticText } from "./morphing-cursor";
import { apiBaseUrl } from "@/lib/env";
import { CheckCircle, MessageCircle } from "lucide-react";
import { TrustedByMarquee } from "../home/trusted-by-section";

function parseConfiguredApiUrl(): URL | null {
    try {
        return new URL(apiBaseUrl());
    } catch {
        return null;
    }
}

function normalizeUrl(url: URL): string {
    return url.toString().replace(/\/+$/, "");
}

function resolveBackendWsBaseUrl(): string {
    const configuredUrl = parseConfiguredApiUrl();
    if (configuredUrl) {
        configuredUrl.protocol = configuredUrl.protocol === "https:" ? "wss:" : "ws:";
        configuredUrl.search = "";
        configuredUrl.hash = "";
        return normalizeUrl(configuredUrl);
    }

    if (typeof window !== "undefined") {
        const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        return `${wsProtocol}//${window.location.hostname}:8000/api/v1`;
    }

    return "ws://127.0.0.1:8000/api/v1";
}

type AIState = "idle" | "connecting" | "listening" | "processing" | "speaking";

const MIC_WORKLET_PATH = "/worklets/pcm16-capture-processor.js";

// Single voice agent - Tessa
const SOPHIA = {
    id: "tessa",
    name: "Tessa",
    gender: "female",
    description: "Kind Companion",
};

const AudioVisualizer: React.FC<{ isActive: boolean; audioLevel: number }> = ({ isActive, audioLevel }) => {
    const [time, setTime] = useState(0);

    useEffect(() => {
        if (!isActive) return;
        let rafId = 0;
        const tick = (t: number) => {
            setTime(t);
            rafId = requestAnimationFrame(tick);
        };
        rafId = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(rafId);
    }, [isActive]);

    if (!isActive) return null;

    return (
        <div className="flex items-end justify-center gap-1 h-5 mt-1">
            {[...Array(5)].map((_, i) => (
                <div
                    key={i}
                    className="w-1 rounded-full transition-all duration-75"
                    style={{
                        height: `${Math.max(3, 6 + audioLevel * 12 + Math.sin(time / 120 + i) * (2 + audioLevel * 2))}px`,
                        background: `linear-gradient(to top, #6366f1, #818cf8, #a5b4fc)`,
                        opacity: 0.8 + audioLevel * 0.2,
                    }}
                />
            ))}
        </div>
    );
};

interface HeroProps {
    title: string;
    description: string | string[];
    stats?: Array<{ label: string; value: string }>;
    adjustForNavbar?: boolean;
}

export const Hero: React.FC<HeroProps> = ({ title, description, stats, adjustForNavbar = false }) => {
    const [aiState, setAiState] = useState<AIState>("idle");
    const [audioLevel, setAudioLevel] = useState(0);
    const [error, setError] = useState<string | null>(null);

    const sectionRef = useRef<HTMLElement | null>(null);
    const heroContentRef = useRef<HTMLDivElement | null>(null);

    const wsRef = useRef<WebSocket | null>(null);
    const connectingRef = useRef<boolean>(false);

    // Audio playback refs (for TTS from backend)
    const audioContextRef = useRef<AudioContext | null>(null);
    const audioInitPromiseRef = useRef<Promise<void> | null>(null);
    const playbackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
    const nextPlaybackTimeRef = useRef<number>(0);
    const ttsSampleRateRef = useRef<number>(24000);
    const awaitingPlaybackCompleteRef = useRef<boolean>(false);
    const dropIncomingAudioRef = useRef<boolean>(false);
    // Generation counter: incremented on every barge-in/reset. Async audio
    // handlers capture this value before awaiting arrayBuffer() and discard
    // their result if the generation has advanced — prevents stale audio from
    // being queued after resetAudioPlayer clears the pipeline.
    const audioGenerationRef = useRef<number>(0);

    // Tessa intro greeting — pre-fetched on mount so it plays instantly on button press.
    const tessaIntroF32Ref = useRef<Float32Array | null>(null);
    const tessaIntroFetchedRef = useRef<boolean>(false);

    // Jitter buffer: collect audio chunks before starting playback to absorb
    // network variance. Prevents stutter caused by irregular chunk arrival.
    const jitterBufferRef = useRef<ArrayBuffer[]>([]);
    const playbackStartedRef = useRef<boolean>(false);
    const JITTER_BUFFER_TARGET_MS = 40; // Smaller buffer = faster barge-in response

    // Microphone refs
    const micStreamRef = useRef<MediaStream | null>(null);
    const micAudioContextRef = useRef<AudioContext | null>(null);
    const processorRef = useRef<ScriptProcessorNode | AudioWorkletNode | null>(null);
    const animationFrameRef = useRef<number | null>(null);
    const analyserRef = useRef<AnalyserNode | null>(null);

    // Track if component is mounted
    const isMountedRef = useRef<boolean>(true);

    const isActive = aiState !== "idle";
    const titleParts = title.split(/\s+/).filter(Boolean);
    const headlineA = (titleParts[0] || "AI").toUpperCase();
    const headlineB = (titleParts.slice(1).join(" ") || "DIALER").toUpperCase();
    const descriptionParagraphs = useMemo(() => {
        const paragraphs = Array.isArray(description) ? description : [description];
        return paragraphs
            .map((text) => text.replace(/\s+/g, " ").trim())
            .filter(Boolean);
    }, [description]);
    const [descriptionIndex, setDescriptionIndex] = useState(0);
    const [descriptionRenderId, setDescriptionRenderId] = useState(0);
    const [typedChars, setTypedChars] = useState(0);

    useEffect(() => {
        if (descriptionParagraphs.length <= 1) return;
        const interval = setInterval(() => {
            setDescriptionIndex((prev) => (prev + 1) % descriptionParagraphs.length);
            setDescriptionRenderId((id) => id + 1);
            setTypedChars(0);
        }, 6000);
        return () => clearInterval(interval);
    }, [descriptionParagraphs.length]);

    useEffect(() => {
        if (descriptionParagraphs.length <= 1) return;
        let rafId = 0;
        const startTime = performance.now();
        const duration = 1200;
        const totalChars = descriptionParagraphs[descriptionIndex]?.length ?? 0;
        const tick = (t: number) => {
            const elapsed = t - startTime;
            const progress = Math.min(1, elapsed / duration);
            setTypedChars(Math.floor(progress * totalChars));
            if (progress < 1) {
                rafId = requestAnimationFrame(tick);
            }
        };
        rafId = requestAnimationFrame(tick);
        return () => cancelAnimationFrame(rafId);
    }, [descriptionIndex, descriptionParagraphs]);

    const handleCtaClick = useCallback(() => {
        const el = document.getElementById("pricing");
        if (el) el.scrollIntoView({ behavior: "smooth" });
    }, []);

    // Initialize browser-native streaming playback using the device's
    // preferred output sample rate.
    const initializeAudioPlayer = useCallback(async () => {
        // Don't initialize if component unmounted
        if (!isMountedRef.current) return;

        // Return existing promise if already loading
        if (audioInitPromiseRef.current) {
            return audioInitPromiseRef.current;
        }

        // Create new initialization promise
        audioInitPromiseRef.current = (async () => {
            try {
                // Let the browser choose the preferred device sample rate.
                if (!audioContextRef.current || audioContextRef.current.state === 'closed') {
                    audioContextRef.current = new AudioContext({
                        latencyHint: 'interactive'
                    });
                    nextPlaybackTimeRef.current = 0;
                }

                const ctx = audioContextRef.current;

                // Resume if suspended (browser autoplay policy).
                // Called inside a user-gesture handler so this resolves immediately.
                if (ctx.state === 'suspended') {
                    await ctx.resume();
                }

                if (ctx.state !== 'running') {
                    throw new Error(`AudioContext not running: ${ctx.state}`);
                }
            } catch (err) {
                console.error('Failed to initialize audio player:', err);
                setError('Audio playback error - please click to try again');
                throw err;
            } finally {
                // Clear promise after a delay to allow retry
                setTimeout(() => {
                    audioInitPromiseRef.current = null;
                }, 100);
            }
        })();

        return audioInitPromiseRef.current;
    }, []);

    // Queue PCM16 audio for sample-accurate playback with jitter buffering.
    // Collects chunks into a buffer before starting playback to absorb network
    // variance and prevent stutter from irregular chunk arrival timing.
    const queueAudioChunk = useCallback((buffer: ArrayBuffer, sampleRate: number = 24000) => {
        if (!isMountedRef.current) return;

        const ctx = audioContextRef.current;
        if (!ctx) return;

        const pcm16 = new Int16Array(buffer);
        if (pcm16.length === 0) return;

        // Add to jitter buffer
        jitterBufferRef.current.push(buffer);

        // Calculate total buffered duration
        const totalSamples = jitterBufferRef.current.reduce(
            (sum, buf) => sum + new Int16Array(buf).length,
            0
        );
        const bufferedMs = (totalSamples / sampleRate) * 1000;

        // Only start playback once we have enough buffered audio
        if (!playbackStartedRef.current) {
            if (bufferedMs < JITTER_BUFFER_TARGET_MS) {
                return; // Still buffering
            }
            playbackStartedRef.current = true;
        }

        // Process all buffered chunks
        const chunksToProcess = [...jitterBufferRef.current];
        jitterBufferRef.current = [];

        for (const chunkBuffer of chunksToProcess) {
            const chunkPcm16 = new Int16Array(chunkBuffer);
            if (chunkPcm16.length === 0) continue;

            const float32 = new Float32Array(chunkPcm16.length);
            for (let i = 0; i < chunkPcm16.length; i++) {
                float32[i] = chunkPcm16[i] / 32768.0;
            }

            const audioBuffer = ctx.createBuffer(1, float32.length, sampleRate);
            audioBuffer.getChannelData(0).set(float32);

            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(ctx.destination);

            const leadTimeSeconds = 0.01;
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
        }
    }, []);

    // Stop queued playback immediately on barge-in without closing the context.
    const resetAudioPlayer = useCallback(() => {
        awaitingPlaybackCompleteRef.current = false;
        // Advance generation so in-flight async handlers discard stale audio
        audioGenerationRef.current += 1;
        playbackSourcesRef.current.forEach((source) => {
            try {
                source.stop();
            } catch (err) {
                console.warn('Reset stop failed:', err);
            }
        });
        playbackSourcesRef.current.clear();
        nextPlaybackTimeRef.current = 0;
        // Clear jitter buffer on reset
        jitterBufferRef.current = [];
        playbackStartedRef.current = false;
    }, []);

    // Full cleanup of audio resources (call on session end)
    const cleanupAudioPlayer = useCallback(() => {
        awaitingPlaybackCompleteRef.current = false;
        playbackSourcesRef.current.forEach((source) => {
            try {
                source.stop();
            } catch { /* ignore */ }
        });
        playbackSourcesRef.current.clear();
        nextPlaybackTimeRef.current = 0;
        // Clear jitter buffer on cleanup
        jitterBufferRef.current = [];
        playbackStartedRef.current = false;
        if (audioContextRef.current) {
            try {
                audioContextRef.current.close();
            } catch { /* ignore */ }
            audioContextRef.current = null;
        }
        audioInitPromiseRef.current = null;
    }, []);

    // Fetch Tessa's intro audio from the preview API on mount so it's ready
    // to play the instant the user presses the button — no synthesis delay.
    const prefetchTessaIntro = useCallback(async () => {
        if (tessaIntroFetchedRef.current) return;
        tessaIntroFetchedRef.current = true;
        try {
            const httpBase = resolveBackendWsBaseUrl()
                .replace(/^wss:/, 'https:')
                .replace(/^ws:/, 'http:');
            const res = await fetch(`${httpBase}/ai-options/voices/preview`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    voice_id: '6ccbfb76-1fc6-48f7-b71d-91ac6298247b',
                    text: "Hi, you've reached the Talk-Lee receptionist team — how can I help you today?",
                }),
            });
            if (!res.ok) { tessaIntroFetchedRef.current = false; return; }
            const data = await res.json() as { audio_base64?: string };
            const b64 = data.audio_base64;
            if (!b64) { tessaIntroFetchedRef.current = false; return; }
            // Decode base64 → raw bytes → Float32Array (f32le PCM at 24 kHz)
            const binary = atob(b64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            tessaIntroF32Ref.current = new Float32Array(bytes.buffer);
            console.log('[TessaIntro] Pre-fetched intro audio:', tessaIntroF32Ref.current.length, 'samples');
        } catch (e) {
            console.warn('[TessaIntro] Prefetch failed — intro will be skipped:', e);
            tessaIntroFetchedRef.current = false; // allow one retry
        }
    }, []);

    // Play the pre-fetched intro buffer through the live AudioContext.
    // Returns a Promise that resolves when audio finishes (or immediately if
    // no intro data is ready). Tracks the source in playbackSourcesRef so
    // barge-in and session cleanup stop it correctly.
    const playTessaIntro = useCallback((): Promise<void> => {
        return new Promise((resolve) => {
            const f32 = tessaIntroF32Ref.current;
            const ctx = audioContextRef.current;
            if (!f32 || !ctx || f32.length === 0) { resolve(); return; }
            try {
                const buf = ctx.createBuffer(1, f32.length, 24000);
                buf.getChannelData(0).set(f32);
                const src = ctx.createBufferSource();
                src.buffer = buf;
                src.connect(ctx.destination);
                playbackSourcesRef.current.add(src);
                // Reserve time in the playback queue so backend TTS chunks
                // schedule after the intro instead of overlapping it.
                const introEndTime = ctx.currentTime + buf.duration;
                nextPlaybackTimeRef.current = introEndTime;
                src.onended = () => {
                    playbackSourcesRef.current.delete(src);
                    resolve();
                };
                src.start(ctx.currentTime);
                console.log('[TessaIntro] Playing intro —', buf.duration.toFixed(1), 's');
            } catch (e) {
                console.warn('[TessaIntro] Playback error:', e);
                resolve();
            }
        });
    }, []);

    const startMicrophone = useCallback(async (): Promise<boolean> => {
        if (micStreamRef.current && processorRef.current && micAudioContextRef.current) {
            return true;
        }

        try {
            // Reuse the pre-warmed stream if available (set by the mount effect
            // when microphone permission was already granted). Avoids the
            // 100-500ms getUserMedia() call on the button-click hot path.
            const stream = micStreamRef.current ?? await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            });
            micStreamRef.current = stream;

            const audioContext = new AudioContext({ sampleRate: 16000 });
            if (audioContext.state === "suspended") {
                await audioContext.resume();
            }
            micAudioContextRef.current = audioContext;
            const source = audioContext.createMediaStreamSource(stream);

            const analyser = audioContext.createAnalyser();
            analyser.fftSize = 256;
            source.connect(analyser);
            analyserRef.current = analyser;
            const dataArray = new Uint8Array(analyser.frequencyBinCount);

            // Throttle audio-level polling to ~30 fps to reduce main-thread load.
            let lastLevelTs = 0;
            const updateLevel = (timestamp: number) => {
                if (!analyserRef.current || !isMountedRef.current) return;
                if (timestamp - lastLevelTs >= 33) {
                    analyserRef.current.getByteFrequencyData(dataArray);
                    const average = dataArray.reduce((a, b) => a + b, 0) / dataArray.length;
                    setAudioLevel(Math.min(1, average / 128));
                    lastLevelTs = timestamp;
                }
                animationFrameRef.current = requestAnimationFrame(updateLevel);
            };
            animationFrameRef.current = requestAnimationFrame(updateLevel);

            const startScriptProcessorFallback = () => {
                const processor = audioContext.createScriptProcessor(1024, 1, 1);
                const silentGain = audioContext.createGain();
                silentGain.gain.value = 0;
                processorRef.current = processor;
                processor.onaudioprocess = (event) => {
                    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
                    const inputData = event.inputBuffer.getChannelData(0);
                    const pcmData = new Int16Array(inputData.length);
                    for (let i = 0; i < inputData.length; i++) {
                        const s = Math.max(-1, Math.min(1, inputData[i]));
                        pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                    }
                    wsRef.current.send(pcmData.buffer);
                };
                source.connect(processor);
                processor.connect(silentGain);
                silentGain.connect(audioContext.destination);
            };

            // AudioWorklet: PCM capture runs entirely off the main thread.
            // Falls back to ScriptProcessorNode when AudioWorklet is unavailable
            // or if the browser rejects loading the worklet module.
            if (audioContext.audioWorklet) {
                try {
                    const workletUrl = new URL(MIC_WORKLET_PATH, window.location.origin).toString();
                    await audioContext.audioWorklet.addModule(workletUrl);
                    const workletNode = new AudioWorkletNode(audioContext, "pcm16-processor");
                    processorRef.current = workletNode;
                    workletNode.port.onmessage = (evt: MessageEvent<ArrayBuffer>) => {
                        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
                        wsRef.current.send(evt.data);
                    };
                    source.connect(workletNode);
                } catch (workletErr) {
                    console.warn("[VoiceAgent] AudioWorklet unavailable, falling back to ScriptProcessorNode.", workletErr);
                    startScriptProcessorFallback();
                }
            } else {
                startScriptProcessorFallback();
            }
            return true;
        } catch (err) {
            console.error("[VoiceAgent] Microphone error:", err);
            let errorMsg = "Microphone access denied";
            if (err instanceof DOMException) {
                if (err.name === "NotAllowedError") {
                    errorMsg = "Microphone permission denied. Please allow access in your browser settings.";
                } else if (err.name === "NotFoundError") {
                    errorMsg = "No microphone found. Please connect a microphone.";
                } else if (err.name === "SecurityError") {
                    errorMsg = "Microphone requires HTTPS. Please use a secure connection.";
                } else if (err.name === "AbortError") {
                    errorMsg = "Microphone request was cancelled.";
                } else {
                    errorMsg = `Microphone error: ${err.name} - ${err.message}`;
                }
            } else if (err instanceof Error) {
                errorMsg = `Microphone error: ${err.message}`;
            }
            setError(errorMsg);
            return false;
        }
    }, []);

    const stopMicrophone = useCallback(() => {
        if (animationFrameRef.current) {
            cancelAnimationFrame(animationFrameRef.current);
            animationFrameRef.current = null;
        }
        if (processorRef.current) {
            if (processorRef.current instanceof AudioWorkletNode) {
                processorRef.current.port.close();
            }
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
        analyserRef.current = null;
        setAudioLevel(0);
    }, []);

    const handleMessage = useCallback(async (event: MessageEvent) => {
        const payload = event.data;

        if (payload instanceof ArrayBuffer || payload instanceof Blob) {
            if (dropIncomingAudioRef.current) {
                return;
            }

            // Capture generation before any await — if it changes during the
            // await, a barge-in happened and this chunk is stale.
            const gen = audioGenerationRef.current;

            // Received audio chunk - queue it for playback
            const arrayBuffer = payload instanceof Blob ? await payload.arrayBuffer() : payload;

            // After await: check if barge-in happened while we were decoding
            if (dropIncomingAudioRef.current || audioGenerationRef.current !== gen) {
                return;
            }

            // Initialize playback on first chunk (with retry logic)
            if (!audioContextRef.current) {
                try {
                    await initializeAudioPlayer();
                } catch (err) {
                    console.error("Failed to initialize audio for chunk:", err);
                    return; // Skip this chunk if audio can't be initialized
                }
            }

            // Final generation check after init await
            if (dropIncomingAudioRef.current || audioGenerationRef.current !== gen) {
                return;
            }

            // Queue chunk for sample-accurate playback
            queueAudioChunk(arrayBuffer, ttsSampleRateRef.current);
            setAiState("speaking");
            return;
        }

        if (typeof payload !== "string") {
            console.warn("[VoiceAgent] Ignoring unsupported WS payload type:", typeof payload);
            return;
        }

        let data: Record<string, unknown>;
        try {
            data = JSON.parse(payload) as Record<string, unknown>;
        } catch (parseError) {
            console.warn("[VoiceAgent] Failed to parse WS JSON payload:", parseError);
            return;
        }

        switch (data.type) {
            case "ready":
                if (typeof data.sample_rate === "number" && data.sample_rate > 0) {
                    ttsSampleRateRef.current = data.sample_rate;
                } else {
                    ttsSampleRateRef.current = 24000;
                }
                dropIncomingAudioRef.current = false;
                // Start microphone immediately so user can barge-in during intro
                setAiState("speaking");
                startMicrophoneRef.current?.();
                break;
            case "transcript":
                if (data.is_final && data.text) setAiState("processing");
                break;
            case "llm_response":
                dropIncomingAudioRef.current = false;
                setAiState("speaking");
                break;
            case "turn_complete":
                setAiState("listening");
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
            case "tts_interrupted":
                // User interrupted - reset AudioWorklet (DON'T close AudioContext)
                dropIncomingAudioRef.current = true;
                resetAudioPlayer();
                setAiState("listening");
                break;
            case "error":
                setError(typeof data.message === "string" ? data.message : "Unknown voice error");
                break;
            default:
                break;
        }
    }, [initializeAudioPlayer, queueAudioChunk, resetAudioPlayer]);

    // Store startMicrophone in ref for use in handleMessage
    const startMicrophoneRef = useRef<(() => void) | null>(null);
    useEffect(() => {
        startMicrophoneRef.current = () => { void startMicrophone(); };
    }, [startMicrophone]);

    const endSession = useCallback(() => {
        connectingRef.current = false;
        stopMicrophone();
        ttsSampleRateRef.current = 24000;
        dropIncomingAudioRef.current = false;
        if (wsRef.current) {
            try { wsRef.current.send(JSON.stringify({ type: "end_call" })); } catch { /* ignore */ }
            wsRef.current.close();
            wsRef.current = null;
        }
        // Full cleanup of audio
        cleanupAudioPlayer();
        setAiState("idle");
        setAudioLevel(0);
    }, [stopMicrophone, cleanupAudioPlayer]);

    const startSession = useCallback(async () => {
        setError(null);
        setAiState("connecting");
        connectingRef.current = true;

        // Check HTTPS requirement (mic only works on HTTPS or localhost)
        const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        const isHttps = window.location.protocol === 'https:';
        if (!isLocalhost && !isHttps) {
            setError("Microphone requires HTTPS. Please access via https:// or localhost.");
            setAiState("idle");
            connectingRef.current = false;
            return;
        }

        // Check if mediaDevices is supported
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setError("Your browser doesn't support microphone access. Please use Chrome, Firefox, or Edge.");
            setAiState("idle");
            connectingRef.current = false;
            return;
        }

        // Debug: Check permissions API support
        if (navigator.permissions && navigator.permissions.query) {
            try {
                const permStatus = await navigator.permissions.query({ name: 'microphone' as PermissionName });
                console.log(`[VoiceAgent] Microphone permission state: ${permStatus.state}`);
                if (permStatus.state === 'denied') {
                    setError("Microphone permission is blocked. Please enable it in browser settings and reload.");
                    setAiState("idle");
                    connectingRef.current = false;
                    return;
                }
            } catch (e) {
                console.log('[VoiceAgent] Could not query microphone permission:', e);
            }
        }

        // Ensure clean state
        stopMicrophone();
        cleanupAudioPlayer();

        // Start microphone inside the user click flow to satisfy browser gesture requirements
        const micStarted = await startMicrophone();
        if (!micStarted) {
            setAiState("idle");
            connectingRef.current = false;
            return;
        }

        // Pre-initialize TTS AudioContext now while we still have a user gesture.
        // This avoids lazy initialization on the first incoming audio chunk,
        // which can cause a noticeable gap before playback starts.
        try {
            await initializeAudioPlayer();
        } catch {
            // Non-fatal — handleMessage will retry on the first chunk
        }

        // Play Tessa's pre-fetched intro greeting immediately.
        // The WS connection happens in parallel so the pipeline is warm by the
        // time the intro finishes — user hears no silence at all.
        setAiState("speaking");
        void playTessaIntro();

        const sessionId = `demo-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
        // To test Deepgram Voice Agent API bridge: swap the two lines below
        const wsUrl = `${resolveBackendWsBaseUrl()}/ws/ask-ai/${sessionId}`;
        // const wsUrl = `ws://127.0.0.1:8001/ws/ask-ai/${sessionId}`; // deepgram-agent-test bridge
        console.log(`[VoiceAgent] Connecting to: ${wsUrl}`);

        const ws = new WebSocket(wsUrl);
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;

        // Connection timeout - if no message within 10 seconds, show error
        const connectionTimeout = setTimeout(() => {
            if (connectingRef.current) {
                console.error("[VoiceAgent] Connection timeout");
                setError("Connection timeout. Is the backend running on port 8000?");
                setAiState("idle");
                connectingRef.current = false;
                ws.close();
            }
        }, 10000);

        ws.onopen = () => {
            console.log("[VoiceAgent] WebSocket connected");
        };

        ws.onmessage = (event) => {
            // Clear timeout on first message (backend is responding)
            clearTimeout(connectionTimeout);
            connectingRef.current = false;

            const isBinary = event.data instanceof Blob || event.data instanceof ArrayBuffer;
            console.log("[VoiceAgent] Message received:", isBinary ? "<binary audio>" : event.data);
            void handleMessage(event);
        };

        ws.onerror = (err) => {
            clearTimeout(connectionTimeout);
            connectingRef.current = false;
            console.error("[VoiceAgent] WebSocket error:", err);
            stopMicrophone();
            setError("Connection error. Please try again.");
            setAiState("idle");
            ws.close();
        };

        ws.onclose = (event) => {
            clearTimeout(connectionTimeout);
            connectingRef.current = false;
            console.log(`[VoiceAgent] WebSocket closed: code=${event.code}, reason=${event.reason}`);
            endSession();
        };
    }, [handleMessage, endSession, cleanupAudioPlayer, startMicrophone, stopMicrophone, initializeAudioPlayer, playTessaIntro]);

    // Wrapper for startSession to handle async
    const handleStartSession = useCallback(() => {
        void startSession();
    }, [startSession]);

    // Pre-fetch Tessa's intro audio so it's ready the moment the button is pressed.
    useEffect(() => {
        void prefetchTessaIntro();
    }, [prefetchTessaIntro]);

    // Pre-warm microphone stream on mount if permission is already granted.
    // getUserMedia() takes 100-500ms when called for the first time on button
    // click. By calling it silently here (only when "granted"), the stream is
    // already open and startMicrophone() returns instantly on button click.
    useEffect(() => {
        const prewarm = async () => {
            if (!navigator.mediaDevices?.getUserMedia) return;
            try {
                const perm = await navigator.permissions.query({ name: 'microphone' as PermissionName });
                if (perm.state !== 'granted') return;
                if (micStreamRef.current) return; // already have a stream
                const stream = await navigator.mediaDevices.getUserMedia({
                    audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
                });
                if (isMountedRef.current) {
                    micStreamRef.current = stream;
                } else {
                    stream.getTracks().forEach(t => t.stop());
                }
            } catch {
                // Ignore — startMicrophone() will request on button click
            }
        };
        prewarm();
    }, []);

    // Cleanup on unmount
    useEffect(() => {
        isMountedRef.current = true;
        return () => {
            isMountedRef.current = false;
            endSession();
        };
    }, [endSession]);

    const heroBaseHeight = "min-h-[60vh]";
    const heroHeightClass = adjustForNavbar ? "min-h-[calc(60vh-4rem)]" : heroBaseHeight;

    return (
        <section ref={sectionRef} className={`relative ${heroHeightClass} flex flex-col justify-center overflow-hidden bg-[#0a0a0a]`}>
            <div className="absolute inset-0 z-0 pointer-events-none">
                <div className="absolute top-10 left-10 w-40 h-40 rounded-full bg-indigo-500/10 blur-3xl" />
                <div className="absolute bottom-20 right-20 w-60 h-60 rounded-full bg-indigo-400/10 blur-3xl" />
            </div>

            <div ref={heroContentRef} className="relative z-10 w-full max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-16">
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 items-center">
                    <div className="lg:col-span-7 space-y-8">
                        <h1 className="text-5xl sm:text-6xl lg:text-7xl font-extrabold tracking-tight">
                            <span className="text-white">{headlineA}</span>
                            <br />
                            <MagneticText text={headlineB} className="text-indigo-400 inline-block" />
                        </h1>

                        <div className="relative min-h-[3.5rem]">
                            <p key={`${descriptionRenderId}-visible`} className="text-lg sm:text-xl text-gray-300 leading-relaxed">
                                {descriptionParagraphs[descriptionIndex]?.slice(0, typedChars)}
                                <span className="inline-block w-0.5 h-5 bg-indigo-400 ml-1 animate-pulse align-middle" />
                            </p>
                        </div>

                        {stats && (
                            <div className="flex flex-wrap gap-8 pt-4">
                                {stats.map((stat) => (
                                    <div key={stat.label} className="text-center">
                                        <div className="text-3xl font-bold text-white">{stat.value}</div>
                                        <div className="text-sm text-gray-400 uppercase tracking-wide">{stat.label}</div>
                                    </div>
                                ))}
                            </div>
                        )}

                        <div className="flex flex-col sm:flex-row gap-4 pt-4">
                            <button
                                onClick={handleCtaClick}
                                className="px-8 py-4 bg-indigo-500 hover:bg-indigo-600 text-white font-semibold rounded-xl transition-all duration-200 shadow-lg shadow-indigo-500/25"
                            >
                                Start Free Trial
                            </button>
                            <button className="px-8 py-4 border border-gray-700 hover:border-gray-600 text-gray-300 font-semibold rounded-xl transition-all duration-200 flex items-center justify-center gap-2">
                                <CheckCircle className="w-5 h-5" />
                                No credit card required
                            </button>
                        </div>

                        <TrustedByMarquee />
                    </div>

                    <div className="lg:col-span-5">
                        <div className="relative">
                            <div className="absolute -inset-1 bg-gradient-to-r from-indigo-500 to-purple-600 rounded-2xl blur opacity-25" />
                            <div className="relative bg-[#111111] border border-gray-800 rounded-2xl p-6 shadow-2xl">
                                <div className="flex items-center justify-between mb-6">
                                    <div className="flex items-center gap-3">
                                        <div className={`w-3 h-3 rounded-full ${isActive ? 'bg-green-500 animate-pulse' : 'bg-gray-500'}`} />
                                        <span className="text-white font-medium">{SOPHIA.name}</span>
                                        <span className="text-gray-500 text-sm">{SOPHIA.description}</span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        {aiState === "listening" && <span className="text-xs text-green-400">Listening</span>}
                                        {aiState === "speaking" && <span className="text-xs text-indigo-400">Speaking</span>}
                                        {aiState === "processing" && <span className="text-xs text-yellow-400">Processing</span>}
                                        {aiState === "connecting" && <span className="text-xs text-gray-400">Connecting...</span>}
                                    </div>
                                </div>

                                <div className="h-40 rounded-xl flex items-center justify-center mb-4 overflow-hidden relative">
                                    {isActive ? (
                                        <div className="flex items-center justify-center w-full h-full scale-150">
                                            <AudioVisualizer isActive={true} audioLevel={audioLevel} />
                                        </div>
                                    ) : (
                                        <div className="absolute inset-0 flex items-center justify-center">
                                            <div className="w-20 h-20 rounded-full bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center transition-all duration-300 scale-90">
                                                <MessageCircle className="w-10 h-10 text-indigo-400/50" />
                                            </div>
                                        </div>
                                    )}
                                </div>

                                {error && (
                                    <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm text-center">
                                        {error}
                                    </div>
                                )}

                                <button
                                    onClick={isActive ? endSession : handleStartSession}
                                    className={`w-full py-3 rounded-xl font-semibold transition-all duration-200 ${isActive
                                            ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30 border border-red-500/30'
                                            : 'bg-indigo-500 hover:bg-indigo-600 text-white shadow-lg shadow-indigo-500/25'
                                        }`}
                                >
                                    {isActive ? 'End Conversation' : 'Try Live Demo'}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
};
