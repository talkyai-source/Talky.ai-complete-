"use client";

/*
 * Assistant VOICE mode.
 *
 * Rendered by FloatingAssistant when the user taps the mic. Connects to the
 * backend voice bridge `/api/v1/assistant/voice`, which runs OUR STT (Deepgram
 * Flux) → the SAME tool-enabled agent as the text chat → OUR TTS (Cartesia).
 *
 *   mic Int16 @16k ──▶ ws.send(binary)                         (capture)
 *   ws ──▶ stt_partial / stt_final                             (live transcript: user)
 *   ws ──▶ assistant_message_start / _token / _end            (live transcript: agent)
 *   ws ──▶ edit_proposal / proposal_result                    (confirm cards, e.g. create campaign)
 *   ws ──▶ tts_start + <binary Float32 @24k> + tts_end        (spoken reply, played via Web Audio)
 *
 * Everything is shown as a LIVE TRANSCRIPT so the user sees what was heard,
 * what the agent is saying, and can confirm a proposed campaign.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Mic, X } from "lucide-react";
import { apiBaseUrl } from "@/lib/env";
import { useAccessToken } from "@/lib/auth-hooks";
import { getAssistantWsToken } from "@/lib/assistant-model-api";
import { MarkdownMessage } from "./markdown-message";
import {
    EditProposalCard,
    type ProposalData,
    type ProposalStatus,
    type ProposalCampaign,
} from "./edit-proposal-card";
import type { DiffChange } from "./diff-view";

type VoiceStatus = "connecting" | "listening" | "thinking" | "speaking" | "error" | "closed";

interface VoiceMsg {
    id: string;
    role: "user" | "assistant" | "system";
    content: string;
    proposal?: ProposalData;
}

const MIC_WORKLET_PATH = "/worklets/pcm16-capture-processor.js";
const CAPTURE_SAMPLE_RATE = 16000;
const PLAYBACK_SAMPLE_RATE = 24000;

function uid(): string {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `v_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function resolveVoiceWsUrl(conversationId: string | null): string {
    let base: string;
    try {
        const u = new URL(apiBaseUrl());
        u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
        u.search = "";
        u.hash = "";
        base = u.toString().replace(/\/+$/, "");
    } catch {
        const proto = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
        const host = typeof window !== "undefined" ? window.location.hostname : "127.0.0.1";
        base = `${proto}//${host}:8000/api/v1`;
    }
    const qs = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
    return `${base}/assistant/voice${qs}`;
}

export function AssistantVoiceMode({
    conversationId,
    onClose,
    onConversationId,
}: {
    conversationId: string | null;
    onClose: () => void;
    onConversationId?: (id: string) => void;
}) {
    const [status, setStatus] = useState<VoiceStatus>("connecting");
    const [partial, setPartial] = useState("");
    const [messages, setMessages] = useState<VoiceMsg[]>([]);
    const [micLive, setMicLive] = useState(false);

    const wsRef = useRef<WebSocket | null>(null);
    // Two AudioContexts on purpose: capture MUST run at 16 kHz (what the mic
    // worklet + backend STT expect), but playing the 24 kHz TTS through that
    // same context would silently downsample it to a 8 kHz Nyquist ceiling —
    // audibly duller speech. A dedicated 24 kHz output context keeps fidelity.
    const audioCtxRef = useRef<AudioContext | null>(null);      // capture @16k
    const playbackCtxRef = useRef<AudioContext | null>(null);   // playback @24k
    const micStreamRef = useRef<MediaStream | null>(null);
    const workletRef = useRef<AudioWorkletNode | ScriptProcessorNode | null>(null);
    const srcNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
    // Gapless TTS playback scheduling.
    const nextPlayRef = useRef(0);
    const scrollRef = useRef<HTMLDivElement | null>(null);
    const teardownRef = useRef<() => void>(() => {});

    const accessToken = useAccessToken();

    // --- teardown (idempotent) ---------------------------------------------
    const teardown = useCallback(() => {
        try {
            const node = workletRef.current;
            if (node && "onaudioprocess" in node) {
                (node as ScriptProcessorNode).onaudioprocess = null;
            }
            node?.disconnect();
        } catch {
            /* noop */
        }
        try {
            srcNodeRef.current?.disconnect();
        } catch {
            /* noop */
        }
        workletRef.current = null;
        srcNodeRef.current = null;
        micStreamRef.current?.getTracks().forEach((t) => t.stop());
        micStreamRef.current = null;
        if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
            audioCtxRef.current.close().catch(() => {});
        }
        audioCtxRef.current = null;
        if (playbackCtxRef.current && playbackCtxRef.current.state !== "closed") {
            playbackCtxRef.current.close().catch(() => {});
        }
        playbackCtxRef.current = null;
        const ws = wsRef.current;
        wsRef.current = null;
        if (ws) {
            ws.onopen = null;
            ws.onmessage = null;
            ws.onerror = null;
            ws.onclose = null;
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                try {
                    ws.close();
                } catch {
                    /* noop */
                }
            }
        }
    }, []);
    teardownRef.current = teardown;

    // --- TTS playback ------------------------------------------------------
    const playChunk = useCallback((buffer: ArrayBuffer) => {
        if (buffer.byteLength === 0) return;
        // Lazy 24 kHz output context (created after the mic-tap gesture, so
        // autoplay policy is satisfied). Kept separate from the 16 kHz capture
        // context — see the ref comments above.
        let ctx = playbackCtxRef.current;
        if (!ctx || ctx.state === "closed") {
            try {
                const AudioCtor =
                    window.AudioContext ||
                    (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
                ctx = new AudioCtor({ sampleRate: PLAYBACK_SAMPLE_RATE });
                playbackCtxRef.current = ctx;
            } catch {
                return;
            }
        }
        if (ctx.state === "suspended") {
            ctx.resume().catch(() => {});
        }
        // Backend sends float32 PCM @24k. Guard against a truncated tail.
        const usable = buffer.byteLength - (buffer.byteLength % 4);
        if (usable <= 0) return;
        const f32 = new Float32Array(buffer, 0, usable / 4);
        try {
            const audioBuffer = ctx.createBuffer(1, f32.length, PLAYBACK_SAMPLE_RATE);
            audioBuffer.getChannelData(0).set(f32);
            const node = ctx.createBufferSource();
            node.buffer = audioBuffer;
            node.connect(ctx.destination);
            const startAt = Math.max(ctx.currentTime, nextPlayRef.current);
            node.start(startAt);
            nextPlayRef.current = startAt + audioBuffer.duration;
        } catch {
            /* drop a bad frame rather than kill the stream */
        }
    }, []);

    // --- proposal actions --------------------------------------------------
    const sendProposalAction = useCallback((proposalId: string, action: "apply" | "reject") => {
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        try {
            ws.send(
                JSON.stringify({
                    type: action === "apply" ? "apply_proposal" : "reject_proposal",
                    proposal_id: proposalId,
                }),
            );
        } catch {
            /* socket closing */
        }
    }, []);

    // --- mic capture -------------------------------------------------------
    const startMic = useCallback(async () => {
        if (!navigator.mediaDevices?.getUserMedia) {
            setMessages((p) => [...p, { id: uid(), role: "system", content: "Microphone not available in this browser." }]);
            return;
        }
        const sendPcm = (buf: ArrayBuffer) => {
            const ws = wsRef.current;
            if (ws && ws.readyState === WebSocket.OPEN) {
                try {
                    ws.send(buf);
                } catch {
                    /* socket closing */
                }
            }
        };
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: CAPTURE_SAMPLE_RATE,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                },
            });
            micStreamRef.current = stream;
            const AudioCtor =
                window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
            const ctx = new AudioCtor({ sampleRate: CAPTURE_SAMPLE_RATE });
            audioCtxRef.current = ctx;
            // CRITICAL: startMic() runs from the WS "ready" handler — many async
            // hops after the user's tap — so this context is created OUTSIDE the
            // user-activation window and starts "suspended". A suspended context
            // never pulls the capture graph, so the worklet's process() never
            // fires and NO mic audio is sent ("not listening at all"). Resume it.
            if (ctx.state === "suspended") {
                try {
                    await ctx.resume();
                } catch {
                    /* best effort */
                }
            }
            const source = ctx.createMediaStreamSource(stream);
            srcNodeRef.current = source;

            // Prefer the AudioWorklet (off-main-thread PCM16 capture); fall back
            // to a ScriptProcessor if the worklet module can't load (older
            // browsers / CSP / 404). Same dual-path the working test-agent uses.
            let usedWorklet = false;
            if (ctx.audioWorklet) {
                try {
                    const workletUrl = new URL(MIC_WORKLET_PATH, window.location.origin).toString();
                    await ctx.audioWorklet.addModule(workletUrl);
                    const worklet = new AudioWorkletNode(ctx, "pcm16-processor");
                    worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => sendPcm(e.data);
                    source.connect(worklet);
                    workletRef.current = worklet;
                    usedWorklet = true;
                } catch (werr) {
                    console.warn("[voice] AudioWorklet unavailable, falling back to ScriptProcessor", werr);
                }
            }
            if (!usedWorklet) {
                const processor = ctx.createScriptProcessor(2048, 1, 1);
                const silent = ctx.createGain();
                silent.gain.value = 0;
                processor.onaudioprocess = (ev) => {
                    const input = ev.inputBuffer.getChannelData(0);
                    const pcm = new Int16Array(input.length);
                    for (let i = 0; i < input.length; i++) {
                        const s = Math.max(-1, Math.min(1, input[i]));
                        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
                    }
                    sendPcm(pcm.buffer);
                };
                source.connect(processor);
                // A ScriptProcessor only runs while connected toward the
                // destination; route it through a muted gain so it's pulled
                // without echoing the mic back to the speakers.
                processor.connect(silent);
                silent.connect(ctx.destination);
                workletRef.current = processor;
            }
            console.info("[voice] mic started; ctx.state =", ctx.state, "worklet =", usedWorklet);
            setMicLive(true);
        } catch (err) {
            console.error("[voice] mic error", err);
            setMessages((p) => [
                ...p,
                { id: uid(), role: "system", content: "Couldn't access the microphone. Check the browser permission and try again." },
            ]);
            setStatus("error");
        }
    }, []);

    // --- connect -----------------------------------------------------------
    useEffect(() => {
        let cancelled = false;
        (async () => {
            const ticket = await getAssistantWsToken();
            const authToken = ticket ?? accessToken;
            if (cancelled) return;
            let ws: WebSocket;
            try {
                ws = new WebSocket(resolveVoiceWsUrl(conversationId));
            } catch {
                setStatus("error");
                return;
            }
            ws.binaryType = "arraybuffer";
            wsRef.current = ws;

            ws.onopen = () => {
                if (authToken) {
                    try {
                        ws.send(JSON.stringify({ type: "auth", token: authToken }));
                    } catch {
                        /* closed */
                    }
                }
            };

            ws.onmessage = (event) => {
                if (event.data instanceof ArrayBuffer) {
                    playChunk(event.data);
                    return;
                }
                let m: {
                    type?: string;
                    text?: string;
                    id?: string;
                    delta?: string;
                    content?: string;
                    conversation_id?: string;
                    proposal_id?: string;
                    tool?: string;
                    warnings?: string[];
                    changes?: DiffChange[];
                    campaigns?: ProposalCampaign[];
                    applied?: boolean;
                    error?: string;
                };
                try {
                    m = JSON.parse(event.data as string);
                } catch {
                    return;
                }
                switch (m.type) {
                    case "ready":
                        setStatus("listening");
                        startMic();
                        if (m.conversation_id && m.conversation_id !== "new") onConversationId?.(m.conversation_id);
                        break;
                    case "conversation_created":
                        if (typeof m.conversation_id === "string") onConversationId?.(m.conversation_id);
                        break;
                    case "stt_partial":
                        setPartial(typeof m.text === "string" ? m.text : "");
                        break;
                    case "stt_final":
                        setPartial("");
                        if (m.text?.trim()) {
                            setMessages((p) => [...p, { id: uid(), role: "user", content: m.text as string }]);
                        }
                        setStatus("thinking");
                        break;
                    case "assistant_typing":
                        setStatus(m.content ? "thinking" : "listening");
                        break;
                    case "assistant_message_start": {
                        const sid = typeof m.id === "string" ? m.id : uid();
                        setMessages((p) => [...p, { id: sid, role: "assistant", content: "" }]);
                        break;
                    }
                    case "assistant_token": {
                        const tid = typeof m.id === "string" ? m.id : null;
                        const delta = typeof m.delta === "string" ? m.delta : "";
                        if (!tid || !delta) break;
                        setMessages((p) => p.map((x) => (x.id === tid ? { ...x, content: x.content + delta } : x)));
                        break;
                    }
                    case "assistant_message_end": {
                        const eid = typeof m.id === "string" ? m.id : null;
                        if (eid && typeof m.content === "string") {
                            const c = m.content;
                            setMessages((p) => p.map((x) => (x.id === eid ? { ...x, content: c } : x)));
                        }
                        break;
                    }
                    case "assistant_message":
                        setMessages((p) => [
                            ...p,
                            { id: uid(), role: "assistant", content: typeof m.content === "string" ? m.content : "" },
                        ]);
                        break;
                    case "tts_start":
                        nextPlayRef.current = 0;
                        setStatus("speaking");
                        break;
                    case "tts_end":
                        setStatus("listening");
                        break;
                    case "edit_proposal": {
                        const pid = typeof m.proposal_id === "string" ? m.proposal_id : uid();
                        setMessages((p) => [
                            ...p,
                            {
                                id: pid,
                                role: "assistant",
                                content: "",
                                proposal: {
                                    proposalId: pid,
                                    tool: typeof m.tool === "string" ? m.tool : "",
                                    warnings: Array.isArray(m.warnings) ? m.warnings : undefined,
                                    changes: Array.isArray(m.changes) ? m.changes : undefined,
                                    campaigns: Array.isArray(m.campaigns) ? m.campaigns : undefined,
                                    status: "pending",
                                },
                            },
                        ]);
                        break;
                    }
                    case "proposal_result": {
                        const pid = typeof m.proposal_id === "string" ? m.proposal_id : null;
                        if (!pid) break;
                        const applied = Boolean(m.applied);
                        const errText = typeof m.error === "string" ? m.error : undefined;
                        setMessages((p) =>
                            p.map((x) => {
                                if (x.id !== pid || !x.proposal) return x;
                                const st: ProposalStatus = applied ? "applied" : errText ? "error" : "rejected";
                                return { ...x, proposal: { ...x.proposal, status: st, error: errText } };
                            }),
                        );
                        break;
                    }
                    case "error":
                        setMessages((p) => [...p, { id: uid(), role: "system", content: typeof m.content === "string" ? m.content : "Voice error." }]);
                        break;
                    default:
                        break;
                }
            };

            ws.onerror = () => setStatus("error");
            ws.onclose = () => {
                if (wsRef.current !== ws) return;
                setStatus("closed");
                setMicLive(false);
            };
        })();
        return () => {
            cancelled = true;
            teardownRef.current();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [conversationId]);

    useEffect(() => {
        scrollRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }, [messages, partial, status]);

    const statusLabel = useMemo(() => {
        switch (status) {
            case "connecting":
                return "Connecting…";
            case "listening":
                return micLive ? "Listening — speak now" : "Ready";
            case "thinking":
                return "Thinking…";
            case "speaking":
                return "Speaking…";
            case "error":
                return "Voice error";
            default:
                return "Voice ended";
        }
    }, [status, micLive]);

    const endVoice = useCallback(() => {
        teardownRef.current();
        onClose();
    }, [onClose]);

    return (
        <div className="flex h-full flex-col">
            {/* Transcript */}
            <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
                {messages.length === 0 && status !== "error" && (
                    <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 p-3 text-[12px] text-foreground">
                        Say something — e.g. <em>“Create a new campaign”</em>, <em>“Show today&apos;s calls”</em>, or
                        <em> “Which campaigns are running?”</em>. I&apos;ll walk you through creating a campaign one question at a time.
                    </div>
                )}
                {messages.map((msg) =>
                    msg.proposal ? (
                        <div key={msg.id} className="flex justify-start">
                            <div className="max-w-[92%]">
                                <EditProposalCard
                                    proposal={msg.proposal}
                                    onApply={(id) => sendProposalAction(id, "apply")}
                                    onReject={(id) => sendProposalAction(id, "reject")}
                                />
                            </div>
                        </div>
                    ) : msg.role === "system" ? (
                        <div
                            key={msg.id}
                            className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300"
                        >
                            {msg.content}
                        </div>
                    ) : (
                        <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                            <div
                                className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${
                                    msg.role === "user" ? "whitespace-pre-wrap bg-cyan-600 text-white" : "bg-muted text-foreground"
                                }`}
                            >
                                {msg.role === "user" ? msg.content : <MarkdownMessage content={msg.content} />}
                            </div>
                        </div>
                    ),
                )}
                {partial && (
                    <div className="flex justify-end">
                        <div className="max-w-[85%] rounded-2xl bg-cyan-600/50 px-3 py-2 text-sm italic text-white">{partial}</div>
                    </div>
                )}
                <div ref={scrollRef} />
            </div>

            {/* Voice status bar */}
            <div className="border-t border-border bg-background px-3 py-3">
                <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div
                            className={`relative inline-flex h-11 w-11 items-center justify-center rounded-full transition-colors ${
                                status === "speaking"
                                    ? "bg-cyan-600 text-white"
                                    : status === "thinking"
                                        ? "bg-muted text-foreground"
                                        : "bg-cyan-600/15 text-cyan-600 dark:text-cyan-400"
                            }`}
                        >
                            {status === "thinking" ? (
                                <Loader2 className="h-5 w-5 animate-spin" />
                            ) : (
                                <Mic className="h-5 w-5" />
                            )}
                            {status === "listening" && micLive && (
                                <span className="absolute inset-0 animate-ping rounded-full bg-cyan-500/30" />
                            )}
                        </div>
                        <span className="text-xs text-muted-foreground">{statusLabel}</span>
                    </div>
                    <button
                        type="button"
                        onClick={endVoice}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
                    >
                        <X className="h-3.5 w-3.5" />
                        End voice
                    </button>
                </div>
            </div>
        </div>
    );
}
