"use client";

import type React from "react";
import { useState, useCallback, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useRouter, usePathname } from "next/navigation";
import { MessageCircle } from "lucide-react";
import { apiBaseUrl } from "@/lib/env";
import { useAuth } from "@/lib/auth-context";
import { ASKAI_SIGNIN_FLAG } from "@/components/auth/askai-signin-notice";
import { ASKAI_CALL_ENDED_FLAG } from "@/components/ui/call-ended-notice";

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

const BAR_COUNT = 14;

// Call-bar heights, mirrored EXACTLY by .askAiCallBar in globals.css
// (103px two-row below 640px, 64px one-row at 640px and above). The
// orb↔bar swap thresholds are navbarTop + these, so the swap happens the
// moment the hero's bottom edge clears the bar's own bottom edge.
const BAR_HEIGHT_TWO_ROW = 103;
const BAR_HEIGHT_ONE_ROW = 64;
// The one-row form's media query — the same sm breakpoint globals.css
// splits .askAiCallBar on. Below it the bar is the phone form, sized by
// CSS alone (88% / 14px); at and above it the bar takes the navbar's
// measured width and radius inline.
const BAR_ONE_ROW_QUERY = "(min-width: 640px)";
// Gap between the show and hide thresholds so scroll jitter exactly on
// the boundary can never make the swap oscillate.
const BAR_SWAP_HYSTERESIS_PX = 24;

const AudioVisualizer: React.FC<{ isActive: boolean; audioLevel: number; bars?: number }> = ({ isActive, audioLevel, bars = BAR_COUNT }) => {
    const barsRef = useRef<(HTMLDivElement | null)[]>([]);
    const rafRef = useRef(0);

    useEffect(() => {
        if (!isActive) return;
        const tick = (t: number) => {
            for (let i = 0; i < bars; i++) {
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
    }, [isActive, audioLevel, bars]);

    if (!isActive) return null;

    return (
        <div className="flex items-center justify-center gap-[3px] h-6">
            {[...Array(bars)].map((_, i) => (
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
    // Phase 5 universal-auth-state: subscribe to AuthContext for the
    // sign-in gate. Replaces a getBrowserAuthToken() snapshot inside
    // handleMainButtonClick — which would have been stale if the user
    // logged in or out without re-mounting this component.
    //
    // Gate on `status === "authenticated"` rather than the raw access
    // token: in cookie-only mode the JWT is HttpOnly and JS can't read
    // it, so the bearer is null for fully-logged-in users. The WS still
    // authenticates via the cookie because `wss://api.talkleeai.com`
    // shares the parent `talkleeai.com` domain with the cookie.
    const { status: authStatus } = useAuth();
    const isAuthed = authStatus === "authenticated";
    const [aiState, setAiState] = useState<AIState>("idle");
    const [audioLevel, setAudioLevel] = useState(0);
    const [error, setError] = useState<string | null>(null);
    // Call timer, plain state — never a ref read during render (React
    // Compiler constraint). Reset in startSession (an event handler) and
    // frozen, not cleared, when the call ends so the sticky error bar can
    // keep showing the final duration.
    const [callSeconds, setCallSeconds] = useState(0);
    // True once the hero's bottom edge has scrolled above the call bar's
    // bottom edge — the orb↔bar swap signal, set by the two
    // IntersectionObservers below (no scroll listener).
    const [pastHero, setPastHero] = useState(false);
    // Mirror for event handlers (handleMessage / failConnection) that must
    // know, at error time, whether the bar was the visible UI.
    const pastHeroRef = useRef(false);
    // Keeps the bar mounted, carrying the error message, after a call dies
    // while the bar is on screen — the in-hero error panel is off-screen at
    // that point and would never be seen. Cleared on dismiss.
    const [barSticky, setBarSticky] = useState(false);
    // The navbar's measured geometry. The bar copies its top offset at
    // every size, and its width and radius only in the one-row form (640px
    // and above), so there it occupies the navbar's area on every device by
    // construction instead of duplicating the navbar's CSS. Below 640px the
    // phone form is sized by .askAiCallBar alone (88% / 14px) and nothing
    // but `top` is set inline. Horizontal placement is never copied:
    // .askAiCallBar centres itself with the same left:0/right:0/
    // margin-inline:auto rule the navbar uses, so the two gaps are solved
    // by the browser and can never drift. Null until the first measurement
    // — the CSS fallbacks in .askAiCallBar apply then.
    const [navGeom, setNavGeom] = useState<{ top: number; width: number; radius: string } | null>(null);
    // Whether the one-row form applies (BAR_ONE_ROW_QUERY). Kept in state
    // so the render can decide whether to emit the inline width/radius;
    // refreshed by the same resize path that rebuilds the swap thresholds.
    const [oneRowBar, setOneRowBar] = useState(false);
    const wrapperRef = useRef<HTMLDivElement | null>(null);

    const wsRef = useRef<WebSocket | null>(null);
    const connectingRef = useRef<boolean>(false);

    // Audio playback refs (for TTS from backend)
    const audioContextRef = useRef<AudioContext | null>(null);
    const audioInitPromiseRef = useRef<Promise<void> | null>(null);
    const playbackSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
    const nextPlaybackTimeRef = useRef<number>(0);
    const ttsSampleRateRef = useRef<number>(24000);
    const awaitingPlaybackCompleteRef = useRef<boolean>(false);
    const playbackUtteranceRef = useRef<string | undefined>(undefined);
    const dropIncomingAudioRef = useRef<boolean>(false);
    // Generation counter: incremented on every barge-in/reset. Async audio
    // handlers capture this value before awaiting arrayBuffer() and discard
    // their result if the generation has advanced — prevents stale audio from
    // being queued after resetAudioPlayer clears the pipeline.
    const audioGenerationRef = useRef<number>(0);

    // Tessa intro greeting — fetched on the first interaction with the button.
    const tessaIntroF32Ref = useRef<Float32Array | null>(null);
    // Stable reference to the in-flight prefetch promise so startSession() can
    // await completion when the user clicks before the first-interaction fetch finishes.
    const tessaIntroPromiseRef = useRef<Promise<void> | null>(null);

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

    // Latest `isActive` for the unmount cleanup, which must know whether a
    // call was live when navigation tore this component down. Same pattern
    // as startMicrophoneRef below.
    const isActiveRef = useRef(false);
    useEffect(() => {
        isActiveRef.current = isActive;
    }, [isActive]);

    // Call timer: ticks while the session is active. The count is reset in
    // startSession, not here, so the sticky error bar keeps the final value.
    useEffect(() => {
        if (!isActive) return;
        const id = setInterval(() => setCallSeconds((s) => s + 1), 1000);
        return () => clearInterval(id);
    }, [isActive]);

    // Measure the navbar the call bar mirrors. The navbar is absolutely
    // positioned at the document top and stays in the DOM at every scroll
    // position, so its width is readable whenever the bar needs it. Its
    // left edge is deliberately not read — see navGeom above.
    const measureNav = useCallback(() => {
        const nav = document.querySelector('nav[aria-label="Primary"]');
        if (!nav) return;
        const cs = getComputedStyle(nav);
        const rect = nav.getBoundingClientRect();
        setNavGeom((prev) => {
            const next = {
                top: Number.parseFloat(cs.top) || 8,
                width: rect.width,
                radius: cs.borderRadius || "9999px",
            };
            return prev &&
                prev.top === next.top &&
                prev.width === next.width &&
                prev.radius === next.radius
                ? prev
                : next;
        });
    }, []);

    // The orb↔bar swap. Rule: the bar shows the moment the hero's bottom
    // edge rises above the bar's own bottom edge (navbar top offset + bar
    // height) and reverts once it comes back down past a 24px hysteresis
    // band, so the boundary can never oscillate. IntersectionObservers fire
    // on crossings only — no scroll listener. Thresholds are rebuilt on
    // resize because the navbar offset (8/10px) and the bar height
    // (103/64px) follow their own width breakpoints.
    //
    // The navbar's geometry is re-measured by a ResizeObserver on the
    // navbar element itself, not by the resize listener: its box also
    // changes without any window resize — when the web font swaps in and
    // when a hover/pointer media query flips (DevTools device-type switch)
    // — and the observer fires on every one of those, plus once on observe.
    useEffect(() => {
        const hero = wrapperRef.current?.closest(".heroSectionRoot");
        if (!hero) return;

        let showObserver: IntersectionObserver | null = null;
        let hideObserver: IntersectionObserver | null = null;

        const setup = () => {
            showObserver?.disconnect();
            hideObserver?.disconnect();
            const nav = document.querySelector('nav[aria-label="Primary"]');
            const navTop = nav ? Number.parseFloat(getComputedStyle(nav).top) || 8 : 8;
            const oneRow = window.matchMedia(BAR_ONE_ROW_QUERY).matches;
            setOneRowBar(oneRow);
            const barHeight = oneRow ? BAR_HEIGHT_ONE_ROW : BAR_HEIGHT_TWO_ROW;
            const showEdge = navTop + barHeight;
            const hideEdge = showEdge + BAR_SWAP_HYSTERESIS_PX;
            // With the viewport clipped at the top by `edge`, the hero
            // intersects it exactly while heroRect.bottom > edge.
            showObserver = new IntersectionObserver(
                ([entry]) => {
                    if (!entry.isIntersecting) {
                        pastHeroRef.current = true;
                        setPastHero(true);
                    }
                },
                { rootMargin: `-${showEdge}px 0px 0px 0px`, threshold: 0 },
            );
            hideObserver = new IntersectionObserver(
                ([entry]) => {
                    if (entry.isIntersecting) {
                        pastHeroRef.current = false;
                        setPastHero(false);
                    }
                },
                { rootMargin: `-${hideEdge}px 0px 0px 0px`, threshold: 0 },
            );
            showObserver.observe(hero);
            hideObserver.observe(hero);
        };

        setup();
        measureNav();
        const nav = document.querySelector('nav[aria-label="Primary"]');
        let navObserver: ResizeObserver | null = null;
        if (nav && typeof ResizeObserver !== "undefined") {
            navObserver = new ResizeObserver(() => measureNav());
            navObserver.observe(nav);
        }
        const onResize = () => setup();
        window.addEventListener("resize", onResize);
        return () => {
            showObserver?.disconnect();
            hideObserver?.disconnect();
            navObserver?.disconnect();
            window.removeEventListener("resize", onResize);
        };
    }, [measureNav]);

    // Clears the sticky error bar (and the error itself).
    const dismissBarError = useCallback(() => {
        setBarSticky(false);
        setError(null);
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
    const queueAudioChunk = useCallback((buffer: ArrayBuffer, sampleRate: number = 24000, flush = false) => {
        if (!isMountedRef.current) return;

        const ctx = audioContextRef.current;
        if (!ctx) return;

        const pcm16 = new Int16Array(buffer);
        if (pcm16.length === 0 && !flush) return;

        // Add to jitter buffer
        if (buffer.byteLength) jitterBufferRef.current.push(buffer);

        // Calculate total buffered duration
        const totalSamples = jitterBufferRef.current.reduce(
            (sum, buf) => sum + new Int16Array(buf).length,
            0
        );
        const bufferedMs = (totalSamples / sampleRate) * 1000;

        // Only start playback once we have enough buffered audio
        if (!playbackStartedRef.current) {
            if (bufferedMs < JITTER_BUFFER_TARGET_MS && !flush) {
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

            const generation = audioGenerationRef.current;
            const utterance = playbackUtteranceRef.current;
            source.onended = () => {
                if (generation !== audioGenerationRef.current || utterance !== playbackUtteranceRef.current) return;
                playbackSourcesRef.current.delete(source);
                if (
                    awaitingPlaybackCompleteRef.current &&
                    playbackSourcesRef.current.size === 0 &&
                    wsRef.current?.readyState === WebSocket.OPEN
                ) {
                    awaitingPlaybackCompleteRef.current = false;
                    wsRef.current.send(JSON.stringify({ type: "playback_complete", utterance_id: utterance }));
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
        playbackUtteranceRef.current = undefined;
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

    // Fetch Tessa's intro audio from the preview API so it is ready to play
    // the instant the session starts — no synthesis delay.
    const prefetchTessaIntro = useCallback((): Promise<void> => {
        // Already done.
        if (tessaIntroF32Ref.current) return Promise.resolve();
        // Already in flight — return the same promise so callers can await it.
        if (tessaIntroPromiseRef.current) return tessaIntroPromiseRef.current;

        const promise = (async () => {
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
                if (!res.ok) { tessaIntroPromiseRef.current = null; return; }
                const data = await res.json() as { audio_base64?: string };
                const b64 = data.audio_base64;
                if (!b64) { tessaIntroPromiseRef.current = null; return; }
                // Decode base64 → raw bytes → Float32Array (f32le PCM at 24 kHz)
                const binary = atob(b64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                tessaIntroF32Ref.current = new Float32Array(bytes.buffer);
                console.log('[TessaIntro] Pre-fetched intro audio:', tessaIntroF32Ref.current.length, 'samples');
            } catch (e) {
                console.warn('[TessaIntro] Prefetch failed — intro will be skipped:', e);
                tessaIntroPromiseRef.current = null;
            }
        })();
        tessaIntroPromiseRef.current = promise;
        return promise;
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

    // Latest `startMicrophone`, published for handleMessage below. Declared
    // (and kept in sync) BEFORE the hook that captures it: writing to a ref
    // that an earlier hook already closed over counts as mutating a hook
    // argument under the React Compiler, which is what the immutability rule
    // was reporting here.
    const startMicrophoneRef = useRef<(() => void) | null>(null);
    useEffect(() => {
        startMicrophoneRef.current = () => { void startMicrophone(); };
    }, [startMicrophone]);

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
            case "playback_start":
                resetAudioPlayer();
                playbackUtteranceRef.current = typeof data.utterance_id === "string" ? data.utterance_id : undefined;
                dropIncomingAudioRef.current = false;
                break;
            case "tts_audio_complete":
                if (data.utterance_id !== playbackUtteranceRef.current) break;
                queueAudioChunk(new ArrayBuffer(0), ttsSampleRateRef.current, true);
                awaitingPlaybackCompleteRef.current = true;
                if (
                    playbackSourcesRef.current.size === 0 &&
                    wsRef.current?.readyState === WebSocket.OPEN
                ) {
                    awaitingPlaybackCompleteRef.current = false;
                    wsRef.current.send(JSON.stringify({ type: "playback_complete", utterance_id: playbackUtteranceRef.current }));
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
                // If the bar is the visible UI, keep it mounted to carry this
                // message — the in-hero panel is off-screen at this point.
                if (pastHeroRef.current) setBarSticky(true);
                // Leave the "speaking" state immediately so the UI isn't stuck.
                // The backend also closes the socket on error → onclose runs the
                // full teardown (endSession) while preserving this message.
                setAiState("idle");
                dropIncomingAudioRef.current = true;
                resetAudioPlayer();
                break;
            default:
                break;
        }
    }, [initializeAudioPlayer, queueAudioChunk, resetAudioPlayer]);

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
        setCallSeconds(0);
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

        // Wait for the intro fetch (started on hover/focus/press, or right here
        // if the click was the first interaction) so the user always hears the
        // intro on the first click. Uncached, the request takes ~2s, so the cap
        // is 6s; it only bounds a stalled network. If the fetch is already in
        // flight, calling prefetchTessaIntro() returns the same promise.
        try {
            await Promise.race([
                prefetchTessaIntro(),
                new Promise((resolve) => setTimeout(resolve, 6000)),
            ]);
        } catch { /* Non-fatal — intro will be skipped */ }

        // Play Tessa's pre-fetched intro greeting immediately.
        // The WS connection happens in parallel so the pipeline is warm by the
        // time the intro finishes — user hears no silence at all.
        setAiState("speaking");
        void playTessaIntro();

        const sessionId = `demo-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
        const wsUrl = `${resolveBackendWsBaseUrl()}/ws/ask-ai/${sessionId}`;
        console.log(`[VoiceAgent] Connecting to: ${wsUrl}`);

        const ws = new WebSocket(wsUrl);
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;

        // Tracks whether the server actually accepted the WS upgrade and sent
        // a frame. If onclose fires before this flips true, we know the
        // connection was rejected (auth, route mismatch, rate limit, etc.).
        let serverAccepted = false;

        // Common path: tear everything down and surface a user-visible error.
        // Pass `redirectToLogin=true` when we infer an auth failure so the user
        // doesn't have to figure out what's wrong.
        const failConnection = (message: string, redirectToLogin: boolean) => {
            clearTimeout(connectionTimeout);
            connectingRef.current = false;
            try { ws.onclose = null; } catch { /* ignore */ }
            try { ws.onerror = null; } catch { /* ignore */ }
            try { ws.close(); } catch { /* ignore */ }
            wsRef.current = null;
            stopMicrophone();
            cleanupAudioPlayer();
            setAudioLevel(0);
            setError(message);
            // Connection failures can also land while the user has scrolled
            // past the hero — surface them in the bar, same as call errors.
            if (pastHeroRef.current) setBarSticky(true);
            setAiState("idle");
            if (redirectToLogin) {
                router.push(`/auth/login?next=${encodeURIComponent(pathname)}`);
            }
        };

        // Connection timeout - if no message within 10 seconds, show error
        const connectionTimeout = setTimeout(() => {
            if (connectingRef.current) {
                console.error("[VoiceAgent] Connection timeout");
                failConnection("Connection timeout. Is the backend running on port 8000?", false);
            }
        }, 10000);

        ws.onopen = () => {
            console.log("[VoiceAgent] WebSocket connected");
        };

        ws.onmessage = (event) => {
            // Clear timeout on first message (backend is responding)
            clearTimeout(connectionTimeout);
            connectingRef.current = false;
            serverAccepted = true;

            const isBinary = event.data instanceof Blob || event.data instanceof ArrayBuffer;
            console.log("[VoiceAgent] Message received:", isBinary ? "<binary audio>" : event.data);
            void handleMessage(event);
        };

        ws.onerror = (err) => {
            console.error("[VoiceAgent] WebSocket error:", err);
            // onerror always fires before onclose for a failed connection.
            // Let onclose decide the user-visible message based on the close code.
        };

        ws.onclose = (event) => {
            console.log(`[VoiceAgent] WebSocket closed: code=${event.code}, reason=${event.reason}`);

            // If the server never accepted us, the upgrade was rejected.
            // Common reasons: stale auth, wrong path, rate limit, capacity.
            if (!serverAccepted) {
                // Close codes that indicate auth/policy failure on the server.
                // 1008 = policy violation, 4001/4003/4004 = app-level auth codes.
                const isAuthFailure =
                    event.code === 1008 ||
                    event.code === 4001 ||
                    event.code === 4003 ||
                    event.code === 4004;

                if (isAuthFailure) {
                    failConnection(
                        "Your session has expired. Redirecting to sign in…",
                        true,
                    );
                    return;
                }

                // 1006 (abnormal closure) on a never-accepted WS most often
                // means the server returned 4xx on the upgrade or refused TCP.
                // Treat it as a probable auth/permissions issue but stay on the
                // page — let the user retry without forcing a redirect.
                if (event.code === 1006 || event.code === 0) {
                    failConnection(
                        "Could not start the assistant. Please make sure you are signed in and try again.",
                        false,
                    );
                    return;
                }

                // 1013 = "try again later" (server at capacity).
                if (event.code === 1013) {
                    failConnection(
                        "The assistant is busy right now. Please try again shortly.",
                        false,
                    );
                    return;
                }

                failConnection(
                    `Connection rejected (code ${event.code}). Please try again.`,
                    false,
                );
                return;
            }

            // Normal end-of-session path (user pressed end, or backend closed
            // cleanly). endSession is idempotent.
            clearTimeout(connectionTimeout);
            connectingRef.current = false;
            endSession();
        };
    }, [handleMessage, endSession, cleanupAudioPlayer, startMicrophone, stopMicrophone, initializeAudioPlayer, playTessaIntro, prefetchTessaIntro, router, pathname]);

    const handleMainButtonClick = useCallback(() => {
        if (!isAuthed) {
            // Signed-out: redirect immediately, no message here. The sign-in
            // notice renders on the auth pages, keyed off this session flag.
            try {
                sessionStorage.setItem(ASKAI_SIGNIN_FLAG, "1");
            } catch { /* storage blocked (private mode) — redirect still works */ }
            router.push(`/auth/login?next=${encodeURIComponent(pathname)}`);
            return;
        }
        if (!isActive) {
            void startSession();
        } else {
            endSession();
        }
    }, [isAuthed, isActive, startSession, endSession, router, pathname]);

    // Start fetching Tessa's intro on the user's first interaction with the
    // button (hover, focus or press) instead of on page load, so visitors who
    // never touch it do not trigger a speech-synthesis call. Signed-out users
    // are redirected to login on click, so they never need the intro.
    const warmTessaIntro = useCallback(() => {
        if (!isAuthed) return;
        void prefetchTessaIntro();
    }, [isAuthed, prefetchTessaIntro]);

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
            // Route navigation is the only thing that unmounts this
            // component (homepage-only mount). If a call was live, flag it
            // so the destination page's CallEndedNotice tells the user.
            if (isActiveRef.current) {
                try {
                    sessionStorage.setItem(ASKAI_CALL_ENDED_FLAG, "1");
                } catch { /* storage blocked — the call still ends below */ }
            }
            endSession();
        };
    }, [endSession]);

    const getStatusText = () => {
        switch (aiState) {
            default: return "Click to talk";
        }
    };

    const barVisible = (isActive && pastHero) || barSticky;
    const timerText = `${String(Math.floor(callSeconds / 60)).padStart(2, "0")}:${String(callSeconds % 60).padStart(2, "0")}`;
    const barStateWord = barSticky && !isActive
        ? "Error"
        : aiState === "listening"
            ? "Listening"
            : aiState === "processing"
                ? "Thinking"
                : aiState === "speaking"
                    ? "Speaking"
                    : "Connecting";

    return (
        <>
        {/* absolute, not fixed: anchors to the hero section (relative) and
            scrolls away with it — ordinary hero content on every device.
            While the pinned call bar is the visible UI, the orb fades and
            scales out here; the swap is render-output only, so the call
            (refs above) is never touched. */}
        <div
            ref={wrapperRef}
            className={`absolute bottom-5 left-2 sm:bottom-6 sm:left-3 z-50 flex items-center gap-2 transition-[opacity,transform] duration-200 ${
                isActive && pastHero ? "pointer-events-none opacity-0 scale-90" : "pointer-events-auto opacity-100 scale-100"
            }`}
        >
            <div className="relative">
                <button
                    onClick={handleMainButtonClick}
                    onPointerEnter={warmTessaIntro}
                    onPointerDown={warmTessaIntro}
                    onFocus={warmTessaIntro}
                    aria-label="Ask AI"
                    className={`relative rounded-full transition-[background-color,border-color,box-shadow,transform] duration-500 ease-out cursor-pointer group ${isActive ? "overflow-visible" : "overflow-hidden"} focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background ${!isActive
                        ? "stats-card inline-flex items-center justify-center h-11 w-[72px] px-0 bg-cyan-50/70 border border-cyan-200/80 shadow-[0_25px_50px_-12px_rgba(0,0,0,0.15)] backdrop-blur-sm transition-[background-color,border-color,box-shadow,transform,width,padding] hover:scale-105 md:justify-start md:gap-2 md:px-3 md:w-[150px] dark:bg-cyan-950/60 dark:border-cyan-200/35 dark:shadow-[0_25px_50px_-12px_rgba(0,0,0,0.55),0_0_0_1px_rgba(34,211,238,0.16),0_0_24px_rgba(34,211,238,0.14)]"
                        : "flex items-center justify-center w-20 h-20 bg-background/70 border-2 border-indigo-400/40 backdrop-blur-md transition-[width,height]"
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
                            <span className="md:hidden text-sm font-semibold leading-none text-primary dark:text-white">ASK AI</span>
                            <span className="relative hidden md:inline-flex h-7 w-7 items-center justify-center rounded-lg bg-cyan-500 shadow-[inset_0_0_0_1px_rgba(255,255,255,0.35)]">
                                <MessageCircle className="h-4 w-4 text-white" />
                                <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-white/90" />
                            </span>
                            <div className="hidden md:flex flex-col items-start leading-tight">
                                <h3 className="text-sm font-semibold leading-none text-primary dark:text-white">Ask AI</h3>
                                <p className="text-[10px] leading-none text-primary/80 dark:text-white/80">{getStatusText()}</p>
                            </div>
                        </div>
                    ) : (
                        <div className="relative z-10 flex flex-col items-center justify-center">
                            {/* Voice-reactive orb — scales + glows with audioLevel.
                                Sole in-flow child of this column, so the button's own
                                flex centring puts the cluster (glow, core, wave bars)
                                exactly on the button's centre — the same centre the
                                ping rings (-inset-2 on the button) already share. */}
                            <div className="relative flex items-center justify-center">
                                <div
                                    className="absolute rounded-full bg-gradient-to-br from-indigo-400/50 to-purple-500/40 blur-md transition-transform duration-100"
                                    style={{
                                        width: `${48 + audioLevel * 56}px`,
                                        height: `${48 + audioLevel * 56}px`,
                                        opacity: 0.55 + audioLevel * 0.45,
                                    }}
                                />
                                <div
                                    className="relative rounded-full bg-gradient-to-br from-indigo-400 via-violet-400 to-fuchsia-400 shadow-[0_0_20px_rgba(129,140,248,0.55)] transition-transform duration-100"
                                    style={{
                                        width: `${36 + audioLevel * 28}px`,
                                        height: `${36 + audioLevel * 28}px`,
                                        transform: `scale(${1 + audioLevel * 0.18})`,
                                    }}
                                />
                                {/* Audio bars overlay on the orb */}
                                <div className="absolute">
                                    <AudioVisualizer isActive={true} audioLevel={audioLevel} />
                                </div>
                            </div>
                        </div>
                    )}

                    {/* State badge — Listening / Thinking / Speaking. Absolutely
                        positioned at the button's bottom centre, outside the orb
                        column, so it no longer pushes the orb cluster above the
                        button's centre. */}
                    {isActive && (
                        <div
                            className={`absolute left-1/2 bottom-1.5 z-10 -translate-x-1/2 whitespace-nowrap px-2.5 py-0.5 rounded-full text-[10px] font-medium tracking-wide leading-none ${
                                aiState === "listening"
                                    ? "bg-emerald-500/20 text-emerald-200 border border-emerald-400/30"
                                    : aiState === "processing"
                                        ? "bg-amber-500/20 text-amber-200 border border-amber-400/30"
                                        : aiState === "speaking"
                                            ? "bg-indigo-500/25 text-indigo-100 border border-indigo-400/40"
                                            : "bg-white/10 text-white/80 border border-white/20"
                            }`}
                        >
                            {aiState === "connecting"
                                ? "Connecting…"
                                : aiState === "listening"
                                    ? "Listening"
                                    : aiState === "processing"
                                        ? "Thinking"
                                        : aiState === "speaking"
                                            ? "Speaking"
                                            : "Idle"}
                        </div>
                    )}
                </button>
            </div>
            {/* In-hero error panel — only while the bar is not carrying the
                error (barSticky); anchored above the button as before. */}
            {error && !barSticky && (
                <div
                    role="alert"
                    className="absolute -top-2 left-0 -translate-y-full w-max max-w-[min(260px,calc(100vw-24px))] rounded-lg bg-red-600/95 text-white text-xs px-3 py-2 shadow-lg shadow-red-500/30 leading-snug"
                >
                    <div className="flex items-start gap-2">
                        <span className="flex-1">{error}</span>
                        <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); setError(null); }}
                            aria-label="Dismiss"
                            className="-m-2.5 p-2.5 opacity-80 hover:opacity-100"
                        >
                            ×
                        </button>
                    </div>
                </div>
            )}
        </div>
        {/* Active-call bar: pinned to the top of the viewport while the
            call is live and the hero has scrolled away (plus the sticky
            error state). Its top offset follows the navbar at every size;
            its width and radius follow the navbar only in the one-row form
            — the phone form is sized by CSS alone. Portaled into the
            homepage's <main id="home"> (document.body as the fallback for
            any other host) purely for z-order: rendered inside the hero's
            z-10 container it would be painted over by every sibling section
            below. Its typeface no longer depends on the target — the root
            layout (layout.tsx) puts `--font-manrope` on <body>, so
            .askAiCallBar's `font-family: var(--font-manrope, inherit)`
            resolves to Manrope under either parent. Rendered by this same
            component instance, so swapping to and from it can never touch
            the call. */}
        {typeof document !== "undefined" && (isActive || barSticky) &&
            createPortal(
                <div
                    className={`askAiCallBar${barVisible ? " askAiCallBarVisible" : ""}`}
                    role="status"
                    style={{
                        top: navGeom?.top,
                        width: oneRowBar ? navGeom?.width : undefined,
                        borderRadius: oneRowBar ? navGeom?.radius : undefined,
                    }}
                >
                    <div className="askAiCallBarRow">
                        <AudioVisualizer isActive={isActive} audioLevel={audioLevel} bars={5} />
                        <span className="askAiCallBarState">{barStateWord}</span>
                        <span className={`askAiCallBarLine${error ? " askAiCallBarErrorText" : ""}`}>
                            {error ?? "Talk-Lee is listening"}
                        </span>
                        <span className="askAiCallBarTimer">{timerText}</span>
                    </div>
                    {isActive ? (
                        <button type="button" className="askAiCallBarEnd" onClick={endSession}>
                            End Call
                        </button>
                    ) : (
                        <button type="button" className="askAiCallBarEnd" onClick={dismissBarError}>
                            Dismiss
                        </button>
                    )}
                </div>,
                document.getElementById("home") ?? document.body,
            )}
        </>
    );
}
