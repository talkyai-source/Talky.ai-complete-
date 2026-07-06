"use client";

// Per-campaign "Test agent" — talk to the SAME agent a real phone call runs.
//
// This opens ONE WebSocket to the backend's /ws/campaign-test/{campaignId}
// endpoint, which builds the session through the identical
// telephony_session_config -> voice_orchestrator path a live call uses (so the
// tenant's AI Options: pipeline_mode / LLM / STT / TTS / persona are honored,
// and change live). The only per-call choice is first-speaker, asked here
// exactly like the Start flow.
//
// The mic-capture / jitter-buffered playback / barge-in logic mirrors the
// proven Ask-AI client (voice-agent-popup.tsx). The one addition: the mic
// AudioContext is created at the backend-advertised `input_sample_rate` (16 kHz
// cascaded, 8 kHz realtime) so caller audio is never speed-shifted.

import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Loader2, Mic, PhoneOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { apiBaseUrl } from "@/lib/env";
import { useAuth } from "@/lib/auth-context";
import { getBrowserAuthToken } from "@/lib/auth-token";

function resolveBackendWsBaseUrl(): string {
    try {
        const u = new URL(apiBaseUrl());
        u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
        u.search = "";
        u.hash = "";
        return u.toString().replace(/\/+$/, "");
    } catch {
        if (typeof window !== "undefined") {
            const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
            return `${wsProto}//${window.location.hostname}:8000/api/v1`;
        }
        return "ws://127.0.0.1:8000/api/v1";
    }
}

const MIC_WORKLET_PATH = "/worklets/pcm16-capture-processor.js";

type Phase = "idle" | "connecting" | "listening" | "processing" | "speaking";

export function TestAgentButton({
    campaignId,
    disabled,
}: {
    campaignId: string;
    disabled?: boolean;
}) {
    const { status: authStatus } = useAuth();
    const isAuthed = authStatus === "authenticated";

    const [modalOpen, setModalOpen] = useState(false);
    const [firstSpeaker, setFirstSpeaker] = useState<"agent" | "user">("agent");
    const [phase, setPhase] = useState<Phase>("idle");
    const [error, setError] = useState<string | null>(null);
    const inCall = phase !== "idle";

    // WebSocket + audio refs (same shape as voice-agent-popup).
    const wsRef = useRef<WebSocket | null>(null);
    const micStreamRef = useRef<MediaStream | null>(null);
    const micCtxRef = useRef<AudioContext | null>(null);
    const micNodeRef = useRef<ScriptProcessorNode | AudioWorkletNode | null>(null);

    const playCtxRef = useRef<AudioContext | null>(null);
    const playSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());
    const nextPlayTimeRef = useRef<number>(0);
    const playRateRef = useRef<number>(24000);
    const jitterRef = useRef<ArrayBuffer[]>([]);
    const playbackStartedRef = useRef<boolean>(false);
    const awaitingPlaybackRef = useRef<boolean>(false);
    const dropAudioRef = useRef<boolean>(false);
    const genRef = useRef<number>(0);
    const JITTER_MS = 40;

    const mountedRef = useRef(true);
    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; };
    }, []);

    // ── Playback ────────────────────────────────────────────────────────
    const queueAudioChunk = useCallback((buf: ArrayBuffer, rate: number) => {
        const ctx = playCtxRef.current;
        if (!ctx) return;
        if (new Int16Array(buf).length === 0) return;
        jitterRef.current.push(buf);

        const totalSamples = jitterRef.current.reduce((s, b) => s + new Int16Array(b).length, 0);
        const bufferedMs = (totalSamples / rate) * 1000;
        if (!playbackStartedRef.current) {
            if (bufferedMs < JITTER_MS) return;
            playbackStartedRef.current = true;
        }
        const chunks = [...jitterRef.current];
        jitterRef.current = [];
        for (const cb of chunks) {
            const pcm = new Int16Array(cb);
            if (pcm.length === 0) continue;
            const f32 = new Float32Array(pcm.length);
            for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768.0;
            const ab = ctx.createBuffer(1, f32.length, rate);
            ab.getChannelData(0).set(f32);
            const src = ctx.createBufferSource();
            src.buffer = ab;
            src.connect(ctx.destination);
            const startAt = Math.max(ctx.currentTime + 0.01, nextPlayTimeRef.current || 0);
            src.onended = () => {
                playSourcesRef.current.delete(src);
                if (
                    awaitingPlaybackRef.current &&
                    playSourcesRef.current.size === 0 &&
                    wsRef.current?.readyState === WebSocket.OPEN
                ) {
                    awaitingPlaybackRef.current = false;
                    wsRef.current.send(JSON.stringify({ type: "playback_complete" }));
                }
            };
            playSourcesRef.current.add(src);
            src.start(startAt);
            nextPlayTimeRef.current = startAt + ab.duration;
        }
    }, []);

    const resetPlayback = useCallback(() => {
        awaitingPlaybackRef.current = false;
        genRef.current += 1;
        playSourcesRef.current.forEach((s) => { try { s.stop(); } catch { /* */ } });
        playSourcesRef.current.clear();
        nextPlayTimeRef.current = 0;
        jitterRef.current = [];
        playbackStartedRef.current = false;
    }, []);

    // ── Microphone (started AFTER `ready`, at the backend's input rate) ──
    const startMicrophone = useCallback(async (inputRate: number): Promise<boolean> => {
        try {
            const stream = micStreamRef.current ?? await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: inputRate,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });
            micStreamRef.current = stream;

            // AudioContext at the required rate: the browser resamples the mic
            // to `inputRate`, so the worklet emits PCM16 at exactly that rate.
            const ctx = new AudioContext({ sampleRate: inputRate });
            if (ctx.state === "suspended") await ctx.resume();
            micCtxRef.current = ctx;
            const source = ctx.createMediaStreamSource(stream);

            const startScriptProcessor = () => {
                const proc = ctx.createScriptProcessor(1024, 1, 1);
                const silent = ctx.createGain();
                silent.gain.value = 0;
                micNodeRef.current = proc;
                proc.onaudioprocess = (ev) => {
                    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
                    const input = ev.inputBuffer.getChannelData(0);
                    const pcm = new Int16Array(input.length);
                    for (let i = 0; i < input.length; i++) {
                        const s = Math.max(-1, Math.min(1, input[i]));
                        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
                    }
                    wsRef.current.send(pcm.buffer);
                };
                source.connect(proc);
                proc.connect(silent);
                silent.connect(ctx.destination);
            };

            if (ctx.audioWorklet) {
                try {
                    const url = new URL(MIC_WORKLET_PATH, window.location.origin).toString();
                    await ctx.audioWorklet.addModule(url);
                    const node = new AudioWorkletNode(ctx, "pcm16-processor");
                    micNodeRef.current = node;
                    node.port.onmessage = (evt: MessageEvent<ArrayBuffer>) => {
                        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
                        wsRef.current.send(evt.data);
                    };
                    source.connect(node);
                } catch {
                    startScriptProcessor();
                }
            } else {
                startScriptProcessor();
            }
            return true;
        } catch (err) {
            let msg = "Microphone access denied";
            if (err instanceof DOMException && err.name === "NotAllowedError") {
                msg = "Microphone permission denied. Allow access in your browser settings.";
            } else if (err instanceof Error) {
                msg = `Microphone error: ${err.message}`;
            }
            setError(msg);
            return false;
        }
    }, []);

    const stopMicrophone = useCallback(() => {
        if (micNodeRef.current) {
            if (micNodeRef.current instanceof AudioWorkletNode) micNodeRef.current.port.close();
            micNodeRef.current.disconnect();
            micNodeRef.current = null;
        }
        if (micCtxRef.current) { try { micCtxRef.current.close(); } catch { /* */ } micCtxRef.current = null; }
        if (micStreamRef.current) {
            micStreamRef.current.getTracks().forEach((t) => t.stop());
            micStreamRef.current = null;
        }
    }, []);

    const cleanupPlayback = useCallback(() => {
        awaitingPlaybackRef.current = false;
        playSourcesRef.current.forEach((s) => { try { s.stop(); } catch { /* */ } });
        playSourcesRef.current.clear();
        nextPlayTimeRef.current = 0;
        jitterRef.current = [];
        playbackStartedRef.current = false;
        if (playCtxRef.current) { try { playCtxRef.current.close(); } catch { /* */ } playCtxRef.current = null; }
    }, []);

    const endSession = useCallback(() => {
        stopMicrophone();
        dropAudioRef.current = false;
        playRateRef.current = 24000;
        if (wsRef.current) {
            try { wsRef.current.send(JSON.stringify({ type: "end_call" })); } catch { /* */ }
            try { wsRef.current.close(); } catch { /* */ }
            wsRef.current = null;
        }
        cleanupPlayback();
        setPhase("idle");
    }, [stopMicrophone, cleanupPlayback]);

    // ── WS message handling ─────────────────────────────────────────────
    const handleMessage = useCallback(async (event: MessageEvent) => {
        const payload = event.data;

        if (payload instanceof ArrayBuffer || payload instanceof Blob) {
            if (dropAudioRef.current) return;
            const gen = genRef.current;
            const ab = payload instanceof Blob ? await payload.arrayBuffer() : payload;
            if (dropAudioRef.current || genRef.current !== gen) return;
            if (!playCtxRef.current) {
                try {
                    playCtxRef.current = new AudioContext({ latencyHint: "interactive" });
                    if (playCtxRef.current.state === "suspended") await playCtxRef.current.resume();
                } catch { return; }
            }
            if (dropAudioRef.current || genRef.current !== gen) return;
            queueAudioChunk(ab, playRateRef.current);
            setPhase("speaking");
            return;
        }
        if (typeof payload !== "string") return;

        let data: Record<string, unknown>;
        try { data = JSON.parse(payload) as Record<string, unknown>; } catch { return; }

        switch (data.type) {
            case "ready": {
                playRateRef.current = typeof data.sample_rate === "number" && data.sample_rate > 0
                    ? data.sample_rate : 24000;
                const inRate = typeof data.input_sample_rate === "number" && data.input_sample_rate > 0
                    ? data.input_sample_rate : 16000;
                dropAudioRef.current = false;
                // Start capturing the moment the agent is ready (also lets the
                // user barge-in through an agent-first greeting).
                setPhase(firstSpeaker === "agent" ? "speaking" : "listening");
                void startMicrophone(inRate);
                break;
            }
            case "transcript":
                if (data.is_final && data.text) setPhase("processing");
                break;
            case "llm_response":
                dropAudioRef.current = false;
                setPhase("speaking");
                break;
            case "turn_complete":
                setPhase("listening");
                break;
            case "tts_audio_complete":
                awaitingPlaybackRef.current = true;
                if (playSourcesRef.current.size === 0 && wsRef.current?.readyState === WebSocket.OPEN) {
                    awaitingPlaybackRef.current = false;
                    wsRef.current.send(JSON.stringify({ type: "playback_complete" }));
                }
                break;
            case "barge_in":
            case "tts_interrupted":
                dropAudioRef.current = true;
                resetPlayback();
                setPhase("listening");
                break;
            case "error":
                setError(typeof data.message === "string" ? data.message : "Voice error");
                dropAudioRef.current = true;
                resetPlayback();
                setPhase("idle");
                break;
            default:
                break;
        }
    }, [firstSpeaker, queueAudioChunk, resetPlayback, startMicrophone]);

    const connect = useCallback(async (fs: "agent" | "user") => {
        setError(null);
        setPhase("connecting");

        const isLocalhost = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
        if (!isLocalhost && window.location.protocol !== "https:") {
            setError("Microphone requires HTTPS. Open the app over https:// or localhost.");
            setPhase("idle");
            return;
        }
        if (!navigator.mediaDevices?.getUserMedia) {
            setError("This browser doesn't support microphone access.");
            setPhase("idle");
            return;
        }

        const wsUrl = `${resolveBackendWsBaseUrl()}/ws/campaign-test/${campaignId}?first_speaker=${fs}`;
        const ws = new WebSocket(wsUrl);
        ws.binaryType = "arraybuffer";
        wsRef.current = ws;

        let accepted = false;
        const fail = (message: string) => {
            try { ws.onclose = null; ws.onerror = null; ws.close(); } catch { /* */ }
            wsRef.current = null;
            stopMicrophone();
            cleanupPlayback();
            setError(message);
            setPhase("idle");
        };
        const timeout = window.setTimeout(() => {
            if (!accepted) fail("Connection timeout. Is the backend running?");
        }, 10000);

        ws.onopen = () => {
            // Cookie auth rides the handshake automatically. When a bearer token
            // is available (cookie-less environments) send it as the first frame
            // — the backend reads the cookie first and ignores this otherwise.
            const token = getBrowserAuthToken();
            if (token) {
                try { ws.send(JSON.stringify({ type: "auth", token })); } catch { /* */ }
            }
        };
        ws.onmessage = (event) => {
            clearTimeout(timeout);
            accepted = true;
            void handleMessage(event);
        };
        ws.onerror = () => { /* onclose decides the message */ };
        ws.onclose = (ev) => {
            if (!accepted) {
                if (ev.code === 1008) { fail("You need to be signed in to test the agent."); return; }
                if (ev.code === 1013) { fail("The test service is busy — try again shortly."); return; }
                fail(`Could not start the test (code ${ev.code}).`);
                return;
            }
            clearTimeout(timeout);
            endSession();
        };
    }, [campaignId, handleMessage, endSession, stopMicrophone, cleanupPlayback]);

    const handleStart = useCallback(() => {
        setModalOpen(false);
        void connect(firstSpeaker);
    }, [connect, firstSpeaker]);

    // Cleanup on unmount.
    useEffect(() => () => { endSession(); }, [endSession]);

    const statusLabel =
        phase === "connecting" ? "Connecting…"
        : phase === "listening" ? "Listening…"
        : phase === "processing" ? "Thinking…"
        : phase === "speaking" ? "Agent speaking…"
        : "";

    return (
        <>
            {inCall ? (
                <Button variant="outline" onClick={endSession}>
                    <PhoneOff className="w-4 h-4" />
                    {statusLabel || "End test"}
                </Button>
            ) : (
                <Button
                    variant="outline"
                    onClick={() => { setError(null); setModalOpen(true); }}
                    disabled={disabled || !isAuthed}
                    title={!isAuthed ? "Sign in to test the agent" : "Talk to this campaign's agent in your browser"}
                >
                    <Mic className="w-4 h-4" />
                    Test agent
                </Button>
            )}

            {error && !inCall && (
                <span role="alert" className="text-xs text-red-600 dark:text-red-400 max-w-[220px]">
                    {error}
                </span>
            )}

            <Modal
                open={modalOpen}
                onOpenChange={setModalOpen}
                title="Test this campaign's agent"
                description="Talk to the exact agent a real call runs — same AI Options (pipeline, LLM, STT, TTS, persona). Pick who opens the conversation."
                size="sm"
                footer={
                    <div className="flex justify-end gap-2">
                        <Button variant="outline" onClick={() => setModalOpen(false)}>Cancel</Button>
                        <Button onClick={handleStart}>
                            {phase === "connecting" ? (
                                <Loader2 className="w-4 h-4 animate-spin" />
                            ) : (
                                <Play className="w-4 h-4" />
                            )}
                            Start test
                        </Button>
                    </div>
                }
            >
                <div className="space-y-2">
                    <label
                        className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                            firstSpeaker === "agent" ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
                        }`}
                    >
                        <input
                            type="radio"
                            name="test-first-speaker"
                            value="agent"
                            checked={firstSpeaker === "agent"}
                            onChange={() => setFirstSpeaker("agent")}
                            className="mt-1"
                        />
                        <div>
                            <div className="text-sm font-medium text-foreground">AI agent speaks first</div>
                            <div className="text-xs text-muted-foreground">
                                The agent greets you the moment the session connects. Same as an agent-first outbound call.
                            </div>
                        </div>
                    </label>

                    <label
                        className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                            firstSpeaker === "user" ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
                        }`}
                    >
                        <input
                            type="radio"
                            name="test-first-speaker"
                            value="user"
                            checked={firstSpeaker === "user"}
                            onChange={() => setFirstSpeaker("user")}
                            className="mt-1"
                        />
                        <div>
                            <div className="text-sm font-medium text-foreground">You speak first</div>
                            <div className="text-xs text-muted-foreground">
                                The agent waits for you to say &ldquo;hello&rdquo; before responding. Same as caller-first.
                            </div>
                        </div>
                    </label>

                    <p className="pt-1 text-xs text-muted-foreground">
                        Your microphone is used only for this test. Nothing is dialed and no plan minutes are used.
                    </p>
                </div>
            </Modal>
        </>
    );
}
