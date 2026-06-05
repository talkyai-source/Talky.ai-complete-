"use client";

/*
 * Floating text-chat assistant.
 *
 * Bottom-left chat bubble that expands into a panel and talks to the
 * backend's `/api/v1/assistant/chat` WebSocket. The backend agent has
 * tools wired for: get_dashboard_stats, get_campaigns, get_recent_calls,
 * start_campaign, initiate_call, send_email, send_sms, book_meeting,
 * schedule_reminder, execute_action_plan, etc. — see
 * `backend/app/infrastructure/assistant/tools.py`.
 *
 * Wire-protocol summary (from assistant_ws.py):
 *
 *   Open:   wss://…/api/v1/assistant/chat?token=<JWT>&conversation_id=<optional>
 *   Send:   { type: "user_message", content: "..." }
 *   Recv:
 *     { type: "connected", message, conversation_id }
 *     { type: "assistant_typing", content: true }
 *     { type: "assistant_message", content: "..." }
 *     { type: "error", content: "..." }
 *
 * Mounted globally by `DashboardLayout` so every authenticated route
 * gets the floater. SSR-disabled because it touches `WebSocket` and
 * `localStorage`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot, Loader2, MessageCircle, Send, X } from "lucide-react";
import { apiBaseUrl } from "@/lib/env";
import { useAccessToken } from "@/lib/auth-hooks";
import { useAuth } from "@/lib/auth-context";
import { getAssistantWsToken } from "@/lib/assistant-model-api";
import { AssistantModelPicker } from "./assistant-model-picker";

/*
 * NOTE on the sidebar offset: this component used to import
 * `useSidebarState` directly to read the sidebar's collapsed/expanded
 * width and offset itself accordingly. Under Next.js 15's Webpack
 * + dynamic({ ssr: false }) loader, that produced a chunk-graph
 * issue: the subscribed store could resolve to `undefined` inside the
 * dynamically-loaded chunk, throwing
 *   "Cannot read properties of undefined (reading 'call')"
 * on every dashboard render.
 *
 * The fix is to push the subscription up into `DashboardLayout` (which
 * already uses the store) and pass the computed offset as a prop. The
 * dynamic chunk now imports nothing from the sidebar module at all.
 */

interface ChatMessage {
    id: string;
    role: "user" | "assistant" | "system";
    content: string;
    ts: number;
}

type WsStatus = "idle" | "connecting" | "open" | "closed" | "error";

function resolveWsBase(): string {
    try {
        const base = new URL(apiBaseUrl());
        base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
        base.search = "";
        base.hash = "";
        return base.toString().replace(/\/+$/, "");
    } catch {
        if (typeof window !== "undefined") {
            const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
            return `${proto}//${window.location.hostname}:8000/api/v1`;
        }
        return "ws://127.0.0.1:8000/api/v1";
    }
}

function uid(): string {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
        return crypto.randomUUID();
    }
    return `m_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

// Floating assistant is anchored to the bottom-right of the viewport.
// Right-anchoring removes the previous sidebar-overlap problem entirely
// — no offset prop or sidebar-state subscription is needed. The header's
// notification bell sits at top-right, so bottom-right is otherwise
// clear of UI chrome.
export function FloatingAssistant() {
    const [open, setOpen] = useState(false);
    const [status, setStatus] = useState<WsStatus>("idle");
    const [typing, setTyping] = useState(false);
    const [messages, setMessages] = useState<ChatMessage[]>([
        {
            id: uid(),
            role: "system",
            content:
                "Hi — I can help with your campaigns, contacts, calls, meetings and more. Try “Show me today's calls”, “Start the Q1 campaign”, or “Book a 30-min call with Daisy tomorrow at 3pm”.",
            ts: Date.now(),
        },
    ]);
    const [input, setInput] = useState("");
    const wsRef = useRef<WebSocket | null>(null);
    const conversationIdRef = useRef<string | null>(null);
    const reconnectAttemptsRef = useRef(0);
    const reconnectTimerRef = useRef<number | null>(null);
    const messagesEndRef = useRef<HTMLDivElement | null>(null);
    // Keepalive: ping every ~25s so an idle WS isn't closed by proxies/LBs
    // (the "reconnecting" churn). Cleared on every teardown.
    const keepAliveTimerRef = useRef<number | null>(null);
    // Connect epoch: bumped on every teardown so an async connect() whose
    // ticket fetch is still in flight can detect it was superseded and abort
    // instead of opening an orphan socket.
    const connectEpochRef = useRef(0);

    // Reactive token via Phase 2's AuthContext-backed hook. Subscribing
    // here means wsUrl re-computes on login, on cross-tab logout (storage
    // event), and on access-token rotation (refresh).
    //
    // AH-Phase-A: the JWT is no longer embedded in the WS URL. URL
    // carries only the resumable conversation_id.
    //
    // AH-Phase-F2: gate now keys on `user` (came from /auth/me, which
    // works via HttpOnly cookies) rather than `accessToken` (which is
    // null when the Bearer fallback is disabled). The backend tries
    // the talky_at cookie before falling back to the first-frame
    // {type:"auth",token} message:
    //   - Bearer fallback ON:  accessToken is set → frontend sends
    //     auth frame on open → backend uses it.
    //   - Bearer fallback OFF: accessToken is null but cookie travels
    //     on the WS handshake → backend uses cookie, no auth frame
    //     needed.
    // Token rotation still triggers a reconnect because accessToken is
    // in the wsUrl useMemo deps — the connect callback identity
    // changes and the open/close effect tears down + re-connects with
    // the fresh credential set.
    const accessToken = useAccessToken();
    const { user } = useAuth();
    const wsUrl = useMemo(() => {
        // accessToken is in the deps below (not interpolated into the
        // URL) so a token rotation invalidates the memo identity and
        // triggers a reconnect with the new credential set.
        void accessToken;
        if (!user) return null;
        const params = new URLSearchParams();
        if (conversationIdRef.current) params.set("conversation_id", conversationIdRef.current);
        const qs = params.toString();
        return `${resolveWsBase()}/assistant/chat${qs ? `?${qs}` : ""}`;
    }, [user, accessToken]);

    const isAuthed = wsUrl !== null;

    // When the assistant is opened by an unauthenticated user, send them
    // to /auth/login with a from=assistant marker so the post-login
    // redirect returns them to the hero instead of /dashboard. Preserve
    // the current URL as `next` so we land back exactly where the user
    // was chatting (or "/" when called from the homepage).
    const goToSignIn = useCallback(() => {
        if (typeof window === "undefined") return;
        const currentPath = `${window.location.pathname}${window.location.search}`;
        const params = new URLSearchParams({ from: "assistant" });
        // Only set `next` when it's a non-auth path — pointing it back at
        // /auth/login itself would loop.
        if (
            currentPath &&
            !currentPath.startsWith("/auth/")
        ) {
            params.set("next", currentPath || "/");
        }
        window.location.href = `/auth/login?${params.toString()}`;
    }, []);

    // Phase 6 universal-auth-state: closeSocket is the planned-teardown
    // path (panel collapsed, unmount, token rotation, logout). Strip the
    // event handlers off the outgoing socket BEFORE calling .close() so
    // the asynchronous onclose firing later — which would normally
    // trigger the backoff-reconnect — can't clobber a freshly-opened
    // replacement socket. Without this, a token rotation that opens a
    // new ws while the old one is still draining its close would have
    // the old's onclose null out wsRef.current and schedule a redundant
    // reconnect of the now-valid new connection.
    const closeSocket = useCallback(() => {
        // Supersede any in-flight async connect() (its ticket fetch may still be
        // pending) so it won't open an orphan socket after this teardown.
        connectEpochRef.current += 1;
        const ws = wsRef.current;
        wsRef.current = null;
        if (reconnectTimerRef.current !== null) {
            window.clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
        }
        if (keepAliveTimerRef.current !== null) {
            window.clearInterval(keepAliveTimerRef.current);
            keepAliveTimerRef.current = null;
        }
        if (ws) {
            ws.onopen = null;
            ws.onmessage = null;
            ws.onerror = null;
            ws.onclose = null;
            if (ws.readyState !== WebSocket.CLOSED) {
                try {
                    ws.close();
                } catch {
                    /* noop */
                }
            }
        }
    }, []);

    const connect = useCallback(async () => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
        if (!wsUrl) {
            // No token — the panel renders an inline "Sign in" CTA, so we
            // just stay idle. Avoid pushing a duplicate system message
            // every time the panel opens.
            setStatus("idle");
            return;
        }
        setStatus("connecting");
        // Fetch a short-lived ticket over authed HTTP (the cookie works there
        // but NOT on the cross-origin WS handshake) and send it as the auth
        // frame in onopen. This HTTP call doubles as the authoritative
        // "am I still logged in?" probe: if it can't mint a ticket (and there's
        // no bearer fallback), the session is genuinely gone.
        const myEpoch = connectEpochRef.current;
        const wsTicket = await getAssistantWsToken();
        // A teardown or newer connect happened while awaiting the ticket — abort
        // so we don't open an orphan socket (async-connect race guard).
        if (myEpoch !== connectEpochRef.current) return;
        if (!wsTicket && !accessToken) {
            // Genuinely unauthenticated (HTTP session lost) — NOT a transient WS
            // drop. Show the sign-in CTA via idle status; do not reconnect-spam.
            setStatus("idle");
            return;
        }
        let ws: WebSocket;
        try {
            ws = new WebSocket(wsUrl);
        } catch {
            setStatus("error");
            return;
        }
        wsRef.current = ws;

        ws.onopen = () => {
            reconnectAttemptsRef.current = 0;
            setStatus("open");
            // Auth via the first {type:"auth",token} frame. The backend tries
            // the talky_at cookie first, but that cookie is NOT reliably sent on
            // the cross-origin WS handshake — so we ALWAYS send a token frame,
            // using the short-lived ticket fetched over authed HTTP (wsTicket,
            // which carries the same identity as the login token). Falls back to
            // the in-memory bearer accessToken for admin / native-shell clients.
            const wsAuthToken = wsTicket ?? accessToken;
            if (wsAuthToken) {
                try {
                    ws.send(JSON.stringify({ type: "auth", token: wsAuthToken }));
                } catch {
                    // Send failure here just means the socket already
                    // closed; onclose will handle status.
                }
            }
            // Keepalive: a periodic app-level ping keeps the idle socket alive
            // through proxies / load-balancers that close quiet connections
            // (~60-120s) — the root cause of the "reconnecting" churn. The
            // backend ignores any non-user_message frame, so this is a no-op
            // server-side beyond resetting the idle timer.
            if (keepAliveTimerRef.current !== null) {
                window.clearInterval(keepAliveTimerRef.current);
            }
            keepAliveTimerRef.current = window.setInterval(() => {
                if (ws.readyState === WebSocket.OPEN) {
                    try {
                        ws.send(JSON.stringify({ type: "ping" }));
                    } catch {
                        /* socket closing; onclose handles reconnect */
                    }
                }
            }, 25_000);
        };

        ws.onmessage = (event) => {
            let payload: { type?: string; content?: unknown; conversation_id?: string };
            try {
                payload = JSON.parse(event.data);
            } catch {
                return;
            }

            switch (payload.type) {
                case "connected":
                    if (payload.conversation_id && payload.conversation_id !== "new") {
                        conversationIdRef.current = payload.conversation_id;
                    }
                    break;
                case "assistant_typing":
                    setTyping(Boolean(payload.content));
                    break;
                case "assistant_message":
                    setTyping(false);
                    setMessages((prev) => [
                        ...prev,
                        {
                            id: uid(),
                            role: "assistant",
                            content: typeof payload.content === "string" ? payload.content : "",
                            ts: Date.now(),
                        },
                    ]);
                    break;
                case "error":
                    setTyping(false);
                    setMessages((prev) => [
                        ...prev,
                        {
                            id: uid(),
                            role: "system",
                            content:
                                typeof payload.content === "string"
                                    ? payload.content
                                    : "Assistant error.",
                            ts: Date.now(),
                        },
                    ]);
                    break;
                default:
                    break;
            }
        };

        ws.onerror = () => {
            setStatus("error");
        };

        ws.onclose = () => {
            // Only act if this is still the active socket (a planned teardown or
            // a newer connect supersedes this one).
            if (wsRef.current !== ws) return;
            wsRef.current = null;
            if (keepAliveTimerRef.current !== null) {
                window.clearInterval(keepAliveTimerRef.current);
                keepAliveTimerRef.current = null;
            }
            // Every close while the panel is open is treated as TRANSIENT (idle
            // drop, network blip, stale ticket) → reconnect with backoff, and
            // each reconnect fetches a FRESH ticket. We deliberately do NOT show
            // "session expired" on a close code: a genuine auth loss is detected
            // in connect() when the ticket can't be minted (→ sign-in CTA). This
            // is what stops a momentary WS hiccup from logging the user out.
            if (open && reconnectAttemptsRef.current < 8) {
                const attempt = reconnectAttemptsRef.current + 1;
                reconnectAttemptsRef.current = attempt;
                setStatus("connecting");
                const delay = Math.min(15_000, 500 * 2 ** attempt);
                reconnectTimerRef.current = window.setTimeout(connect, delay);
            } else {
                setStatus("closed");
            }
        };
    }, [accessToken, open, wsUrl]);

    // Lifecycle: connect when expanded, close when collapsed, AND
    // reconnect when accessToken rotates mid-session. Token rotation
    // flows through wsUrl → connect (deps include wsUrl) → this effect
    // re-runs. The cleanup closes the prior socket cleanly (Phase 6
    // detaches its event handlers first, so the delayed onclose can't
    // misfire); the body opens a new socket with the rotated token.
    //
    // conversationIdRef preserves the assistant_conversations row id
    // across the reconnect, so the backend's resume-on-conversation_id
    // path (assistant_ws.py STEP 3) replays the full message history
    // and the user's chat continues seamlessly across the 15-minute
    // JWT TTL boundary.
    useEffect(() => {
        if (open) {
            // Fresh open → reset the backoff so a prior exhausted reconnect
            // sequence doesn't prevent this open from connecting.
            reconnectAttemptsRef.current = 0;
            connect();
        } else {
            closeSocket();
        }
        return () => {
            // The effect's cleanup also runs on unmount.
            closeSocket();
        };
    }, [open, connect, closeSocket]);

    // Auto-scroll on new messages.
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }, [messages, typing]);

    const sendMessage = useCallback(() => {
        const trimmed = input.trim();
        if (!trimmed) return;
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            setMessages((prev) => [
                ...prev,
                {
                    id: uid(),
                    role: "system",
                    content: "Not connected. Trying to reconnect…",
                    ts: Date.now(),
                },
            ]);
            connect();
            return;
        }
        setMessages((prev) => [
            ...prev,
            { id: uid(), role: "user", content: trimmed, ts: Date.now() },
        ]);
        setInput("");
        setTyping(true);
        try {
            ws.send(JSON.stringify({ type: "user_message", content: trimmed }));
        } catch {
            setTyping(false);
            setMessages((prev) => [
                ...prev,
                {
                    id: uid(),
                    role: "system",
                    content: "Failed to send. Reconnecting…",
                    ts: Date.now(),
                },
            ]);
            connect();
        }
    }, [connect, input]);

    const onInputKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        },
        [sendMessage],
    );

    const statusLabel = useMemo(() => {
        switch (status) {
            case "connecting":
                return "Connecting…";
            case "open":
                return "Online";
            case "closed":
                return "Offline";
            case "error":
                return "Connection error";
            default:
                return "Ready";
        }
    }, [status]);

    return (
        <>
            {/* Collapsed launcher — anchored bottom-right, away from the
                sidebar entirely. Theme tokens (background / foreground /
                border / muted) keep the panel correct in both light and
                dark mode; the cyan brand colour is the same in both. */}
            {!open && (
                <button
                    type="button"
                    onClick={() => setOpen(true)}
                    aria-label="Open AI assistant"
                    className="fixed bottom-5 right-4 sm:bottom-6 sm:right-6 z-50 inline-flex h-12 w-12 items-center justify-center rounded-full bg-cyan-600 text-white shadow-lg ring-1 ring-cyan-900/40 dark:ring-cyan-300/30 transition-[transform,box-shadow] hover:scale-105 hover:bg-cyan-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                    <MessageCircle className="h-5 w-5" />
                </button>
            )}

            {/* Expanded chat panel — same anchor as the launcher. */}
            {open && (
                <div
                    role="dialog"
                    aria-label="AI Assistant"
                    className="fixed bottom-5 right-4 sm:bottom-6 sm:right-6 z-50 flex h-[28rem] w-[22rem] max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-2xl border border-border bg-background text-foreground shadow-2xl"
                >
                    {/* Header */}
                    <div className="flex items-center justify-between gap-2 border-b border-border bg-cyan-600/10 px-4 py-3">
                        <div className="flex items-center gap-2 text-sm">
                            <Bot className="h-5 w-5 text-cyan-600 dark:text-cyan-400" />
                            <div className="flex flex-col leading-tight">
                                <span className="font-semibold text-foreground">Assistant</span>
                                <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                                    {statusLabel}
                                </span>
                            </div>
                        </div>
                        <AssistantModelPicker />
                        <button
                            type="button"
                            onClick={() => setOpen(false)}
                            aria-label="Close assistant"
                            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </div>

                    {/* Messages */}
                    <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
                        {messages.map((msg) => (
                            <MessageRow key={msg.id} msg={msg} />
                        ))}
                        {!isAuthed && (
                            <div className="rounded-lg border border-cyan-500/30 bg-cyan-500/10 p-3 text-[12px] text-foreground">
                                <p className="mb-2">
                                    Sign in to chat with the assistant. We&apos;ll bring you
                                    straight back here after you log in.
                                </p>
                                <button
                                    type="button"
                                    onClick={goToSignIn}
                                    className="inline-flex items-center justify-center rounded-md bg-cyan-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-cyan-500"
                                >
                                    Sign in to continue
                                </button>
                            </div>
                        )}
                        {typing && (
                            <div className="flex items-center gap-2 text-xs text-muted-foreground px-2">
                                <Loader2 className="h-3 w-3 animate-spin" />
                                Assistant is thinking…
                            </div>
                        )}
                        <div ref={messagesEndRef} />
                    </div>

                    {/* Composer */}
                    <div className="border-t border-border bg-background px-3 py-2">
                        <div className="flex items-end gap-2">
                            <textarea
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={onInputKeyDown}
                                placeholder={
                                    !isAuthed
                                        ? "Sign in to start chatting…"
                                        : status === "open"
                                            ? "Ask anything — “show today's calls”, “start campaign X”…"
                                            : "Reconnecting…"
                                }
                                rows={1}
                                className="flex-1 resize-none rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                disabled={!isAuthed || status !== "open"}
                            />
                            <button
                                type="button"
                                onClick={isAuthed ? sendMessage : goToSignIn}
                                disabled={isAuthed && (status !== "open" || !input.trim())}
                                aria-label={isAuthed ? "Send message" : "Sign in"}
                                className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-cyan-600 text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                <Send className="h-4 w-4" />
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}

function MessageRow({ msg }: { msg: ChatMessage }) {
    if (msg.role === "system") {
        return (
            <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300">
                {msg.content}
            </div>
        );
    }
    const isUser = msg.role === "user";
    return (
        <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
            <div
                className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-sm ${
                    isUser
                        ? "bg-cyan-600 text-white"
                        : "bg-muted text-foreground"
                }`}
            >
                {msg.content}
            </div>
        </div>
    );
}
