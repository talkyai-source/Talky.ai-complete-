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

    // Reactive token via Phase 2's AuthContext-backed hook. Subscribing
    // here means wsUrl re-computes on login, on cross-tab logout (storage
    // event), and on access-token rotation (refresh). The previous
    // implementation snapshot the token via getBrowserAuthToken() at
    // mount with an empty deps array — that ran before AuthContext
    // hydrated localStorage on the first dashboard mount, so wsUrl was
    // null for the lifetime of the component and the panel rendered the
    // "Sign in to chat" CTA despite the user being authenticated. This
    // is the Phase 5 fix for the Ask-AI re-prompt bug.
    const accessToken = useAccessToken();
    const wsUrl = useMemo(() => {
        if (!accessToken) return null;
        const params = new URLSearchParams({ token: accessToken });
        if (conversationIdRef.current) params.set("conversation_id", conversationIdRef.current);
        return `${resolveWsBase()}/assistant/chat?${params.toString()}`;
    }, [accessToken]);

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

    const closeSocket = useCallback(() => {
        const ws = wsRef.current;
        wsRef.current = null;
        if (reconnectTimerRef.current !== null) {
            window.clearTimeout(reconnectTimerRef.current);
            reconnectTimerRef.current = null;
        }
        if (ws && ws.readyState !== WebSocket.CLOSED) {
            try {
                ws.close();
            } catch {
                /* noop */
            }
        }
    }, []);

    const connect = useCallback(() => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
        if (!wsUrl) {
            // No token — the panel renders an inline "Sign in" CTA, so we
            // just stay idle. Avoid pushing a duplicate system message
            // every time the panel opens.
            setStatus("idle");
            return;
        }
        setStatus("connecting");
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
            wsRef.current = null;
            setStatus("closed");
            // Reconnect with backoff while the panel is open. Cap the
            // attempts so a permanently-down backend doesn't hammer.
            if (open && reconnectAttemptsRef.current < 5) {
                const attempt = reconnectAttemptsRef.current + 1;
                reconnectAttemptsRef.current = attempt;
                const delay = Math.min(15_000, 500 * 2 ** attempt);
                reconnectTimerRef.current = window.setTimeout(connect, delay);
            }
        };
    }, [open, wsUrl]);

    // Open / close lifecycle: connect when expanded, close when collapsed.
    useEffect(() => {
        if (open) {
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
