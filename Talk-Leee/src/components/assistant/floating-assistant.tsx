"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Bot, X, Send, Sparkles, Loader2, MessageSquare, Minimize2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getBrowserAuthToken } from "@/lib/auth-token";
import { apiBaseUrl } from "@/lib/env";
import { useAuth } from "@/lib/auth-context";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatMessage {
    id: string;
    role: "user" | "assistant" | "system";
    content: string;
    timestamp: Date;
}

type ConnectionStatus = "disconnected" | "connecting" | "connected" | "error";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function generateId() {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
    return `msg_${Math.random().toString(36).slice(2)}_${Date.now()}`;
}

function getWsBaseUrl(): string {
    const base = apiBaseUrl();
    // Convert http(s) to ws(s)
    return base.replace(/^http/, "ws");
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FloatingAssistant() {
    const { user } = useAuth();
    const [isOpen, setIsOpen] = useState(false);
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [inputValue, setInputValue] = useState("");
    const [isTyping, setIsTyping] = useState(false);
    const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("disconnected");
    const [conversationId, setConversationId] = useState<string | null>(null);
    const [hasUnread, setHasUnread] = useState(false);

    const wsRef = useRef<WebSocket | null>(null);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLTextAreaElement>(null);
    const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const reconnectAttemptRef = useRef(0);
    const isOpenRef = useRef(isOpen);
    const conversationIdRef = useRef<string | null>(null);

    // Keep ref in sync
    useEffect(() => {
        isOpenRef.current = isOpen;
    }, [isOpen]);

    useEffect(() => {
        conversationIdRef.current = conversationId;
    }, [conversationId]);

    // Auto-scroll to bottom
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, isTyping]);

    // Focus input when opened
    useEffect(() => {
        if (isOpen) {
            setHasUnread(false);
            setTimeout(() => inputRef.current?.focus(), 150);
        }
    }, [isOpen]);

    // WebSocket connection manager
    const connect = useCallback(() => {
        if (!user) return;
        const token = getBrowserAuthToken();
        if (!token) {
            setConnectionStatus("error");
            return;
        }

        // Clean up existing connection
        if (wsRef.current) {
            wsRef.current.onclose = null;
            wsRef.current.onerror = null;
            wsRef.current.onmessage = null;
            wsRef.current.close();
            wsRef.current = null;
        }

        setConnectionStatus("connecting");

        const wsBase = getWsBaseUrl();
        const params = new URLSearchParams({ token });
        if (conversationIdRef.current) params.set("conversation_id", conversationIdRef.current);
        const wsUrl = `${wsBase}/assistant/chat?${params.toString()}`;

        try {
            const ws = new WebSocket(wsUrl);
            wsRef.current = ws;

            ws.onopen = () => {
                reconnectAttemptRef.current = 0;
                // Status will be set to "connected" when we receive the "connected" message
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);

                    switch (data.type) {
                        case "connected":
                            setConnectionStatus("connected");
                            if (data.conversation_id && data.conversation_id !== "new") {
                                conversationIdRef.current = data.conversation_id;
                                setConversationId(data.conversation_id);
                            }
                            break;

                        case "assistant_message": {
                            const msg: ChatMessage = {
                                id: generateId(),
                                role: "assistant",
                                content: data.content || "",
                                timestamp: new Date(),
                            };
                            setMessages((prev) => [...prev, msg]);
                            setIsTyping(false);
                            if (!isOpenRef.current) {
                                setHasUnread(true);
                            }
                            break;
                        }

                        case "assistant_typing":
                            setIsTyping(Boolean(data.content));
                            break;

                        case "conversation_created":
                            if (data.conversation_id) {
                                conversationIdRef.current = data.conversation_id;
                                setConversationId(data.conversation_id);
                            }
                            break;

                        case "error":
                            setMessages((prev) => [
                                ...prev,
                                {
                                    id: generateId(),
                                    role: "system",
                                    content: data.content || "An error occurred.",
                                    timestamp: new Date(),
                                },
                            ]);
                            setIsTyping(false);
                            break;

                        case "pong":
                            // Heartbeat response
                            break;

                        default:
                            break;
                    }
                } catch {
                    // Ignore parse errors
                }
            };

            ws.onclose = () => {
                setConnectionStatus("disconnected");
                wsRef.current = null;

                // Auto-reconnect with exponential backoff
                if (reconnectAttemptRef.current < 5) {
                    const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 16000);
                    reconnectAttemptRef.current += 1;
                    reconnectTimerRef.current = setTimeout(() => {
                        if (isOpenRef.current) {
                            connect();
                        }
                    }, delay);
                } else {
                    setConnectionStatus("error");
                }
            };

            ws.onerror = () => {
                setConnectionStatus("error");
            };
        } catch {
            setConnectionStatus("error");
        }
    }, [user]);

    // Connect when panel opens, disconnect when it closes
    useEffect(() => {
        if (isOpen && user) {
            connect();
        }

        return () => {
            if (reconnectTimerRef.current) {
                clearTimeout(reconnectTimerRef.current);
                reconnectTimerRef.current = null;
            }
        };
    }, [isOpen, user, connect]);

    // Heartbeat to keep connection alive
    useEffect(() => {
        if (connectionStatus !== "connected") return;

        const interval = setInterval(() => {
            if (wsRef.current?.readyState === WebSocket.OPEN) {
                try {
                    wsRef.current.send(JSON.stringify({ type: "ping" }));
                } catch {
                    // Ignore
                }
            }
        }, 25000);

        return () => clearInterval(interval);
    }, [connectionStatus]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
            if (wsRef.current) {
                wsRef.current.onclose = null;
                wsRef.current.close();
                wsRef.current = null;
            }
        };
    }, []);

    const sendMessage = useCallback(() => {
        const content = inputValue.trim();
        if (!content) return;
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

        const userMsg: ChatMessage = {
            id: generateId(),
            role: "user",
            content,
            timestamp: new Date(),
        };
        setMessages((prev) => [...prev, userMsg]);
        setInputValue("");

        try {
            wsRef.current.send(
                JSON.stringify({
                    type: "user_message",
                    content,
                })
            );
        } catch {
            setMessages((prev) => [
                ...prev,
                {
                    id: generateId(),
                    role: "system",
                    content: "Failed to send message. Please try again.",
                    timestamp: new Date(),
                },
            ]);
        }
    }, [inputValue]);

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        },
        [sendMessage]
    );

    const handleNewChat = useCallback(() => {
        // Close existing connection
        if (wsRef.current) {
            wsRef.current.onclose = null;
            wsRef.current.close();
            wsRef.current = null;
        }
        setMessages([]);
        setConversationId(null);
        conversationIdRef.current = null;
        setIsTyping(false);
        reconnectAttemptRef.current = 0;
        // Reconnect
        setTimeout(() => connect(), 100);
    }, [connect]);

    if (!user) return null;

    const statusColor =
        connectionStatus === "connected"
            ? "bg-emerald-500"
            : connectionStatus === "connecting"
                ? "bg-amber-500 animate-pulse"
                : connectionStatus === "error"
                    ? "bg-red-500"
                    : "bg-gray-400";

    const statusText =
        connectionStatus === "connected"
            ? "Connected"
            : connectionStatus === "connecting"
                ? "Connecting..."
                : connectionStatus === "error"
                    ? "Connection error"
                    : "Disconnected";

    return (
        <>
            {/* Floating Action Button */}
            <button
                type="button"
                onClick={() => setIsOpen((prev) => !prev)}
                className={cn(
                    "fixed bottom-6 right-6 z-50 flex items-center justify-center rounded-full shadow-2xl transition-all duration-300 ease-in-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2",
                    isOpen
                        ? "h-12 w-12 bg-gray-800 hover:bg-gray-700 dark:bg-gray-700 dark:hover:bg-gray-600 rotate-0"
                        : "h-14 w-14 bg-gradient-to-br from-teal-500 to-teal-700 hover:from-teal-400 hover:to-teal-600 hover:scale-110 hover:shadow-teal-500/25"
                )}
                aria-label={isOpen ? "Close assistant" : "Open assistant"}
            >
                {isOpen ? (
                    <X className="h-5 w-5 text-white" />
                ) : (
                    <>
                        <Bot className="h-7 w-7 text-white" />
                        {hasUnread && (
                            <span className="absolute -top-0.5 -right-0.5 flex h-4 w-4">
                                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-400 opacity-75" />
                                <span className="relative inline-flex h-4 w-4 rounded-full bg-red-500 border-2 border-white dark:border-gray-900" />
                            </span>
                        )}
                    </>
                )}
            </button>

            {/* Chat Panel */}
            <div
                className={cn(
                    "fixed bottom-24 right-6 z-50 flex flex-col overflow-hidden rounded-2xl border shadow-2xl transition-all duration-300 ease-in-out",
                    "w-[380px] max-h-[560px]",
                    "border-gray-200/80 dark:border-white/10",
                    "bg-white dark:bg-gray-900",
                    isOpen
                        ? "opacity-100 scale-100 translate-y-0 pointer-events-auto"
                        : "opacity-0 scale-95 translate-y-4 pointer-events-none"
                )}
                role="dialog"
                aria-label="AI Assistant Chat"
            >
                {/* Header */}
                <div className="relative flex items-center justify-between px-5 py-4 border-b border-gray-100 dark:border-white/10 bg-gradient-to-r from-teal-600 to-teal-700 dark:from-teal-800 dark:to-teal-900">
                    <div className="flex items-center gap-3">
                        <div className="relative flex h-10 w-10 items-center justify-center rounded-xl bg-white/15 backdrop-blur-sm border border-white/20 shadow-sm">
                            <Bot className="h-5 w-5 text-white" />
                            <span
                                className={cn("absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-teal-600 dark:border-teal-800", statusColor)}
                                title={statusText}
                            />
                        </div>
                        <div>
                            <h3 className="text-sm font-bold text-white leading-tight">Talky Assistant</h3>
                            <p className="text-xs text-teal-100/80 font-medium">{statusText}</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-1">
                        <button
                            type="button"
                            onClick={handleNewChat}
                            className="flex h-8 w-8 items-center justify-center rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
                            aria-label="New conversation"
                            title="New conversation"
                        >
                            <MessageSquare className="h-4 w-4" />
                        </button>
                        <button
                            type="button"
                            onClick={() => setIsOpen(false)}
                            className="flex h-8 w-8 items-center justify-center rounded-lg text-white/70 hover:text-white hover:bg-white/10 transition-colors"
                            aria-label="Minimize"
                        >
                            <Minimize2 className="h-4 w-4" />
                        </button>
                    </div>
                    {/* Decorative gradient bar */}
                    <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-transparent via-white/30 to-transparent" />
                </div>

                {/* Messages Area */}
                <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 min-h-[300px] max-h-[380px] scroll-smooth bg-gray-50/50 dark:bg-gray-900/80">
                    {messages.length === 0 && !isTyping ? (
                        <div className="flex flex-col items-center justify-center h-full text-center py-8">
                            <div className="h-16 w-16 rounded-2xl bg-gradient-to-br from-teal-100 to-teal-50 dark:from-teal-900/30 dark:to-teal-800/20 border border-teal-200/60 dark:border-teal-700/30 flex items-center justify-center mb-4 shadow-sm">
                                <Sparkles className="h-7 w-7 text-teal-600 dark:text-teal-400" />
                            </div>
                            <h4 className="text-sm font-bold text-gray-800 dark:text-gray-200 mb-1">
                                How can I help?
                            </h4>
                            <p className="text-xs text-gray-500 dark:text-gray-400 max-w-[240px] leading-relaxed">
                                Ask me about your calls, campaigns, analytics, or anything else about your account.
                            </p>
                            <div className="mt-5 grid grid-cols-1 gap-2 w-full max-w-[260px]">
                                {[
                                    "How many calls did I make today?",
                                    "Show my campaign stats",
                                    "What's my success rate?",
                                ].map((suggestion) => (
                                    <button
                                        key={suggestion}
                                        type="button"
                                        className="text-xs text-left px-3 py-2.5 rounded-xl border border-gray-200 dark:border-white/10 bg-white dark:bg-gray-800/60 text-gray-700 dark:text-gray-300 hover:bg-teal-50 hover:border-teal-200 hover:text-teal-700 dark:hover:bg-teal-900/20 dark:hover:border-teal-700/30 dark:hover:text-teal-300 transition-colors font-medium"
                                        onClick={() => {
                                            setInputValue(suggestion);
                                            setTimeout(() => inputRef.current?.focus(), 50);
                                        }}
                                    >
                                        {suggestion}
                                    </button>
                                ))}
                            </div>
                        </div>
                    ) : (
                        <>
                            {messages.map((msg) => (
                                <div
                                    key={msg.id}
                                    className={cn(
                                        "flex",
                                        msg.role === "user" ? "justify-end" : "justify-start"
                                    )}
                                >
                                    {msg.role === "assistant" && (
                                        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-teal-100 dark:bg-teal-900/40 border border-teal-200/60 dark:border-teal-700/30 mr-2 mt-0.5 shrink-0">
                                            <Bot className="h-3.5 w-3.5 text-teal-700 dark:text-teal-400" />
                                        </div>
                                    )}
                                    <div
                                        className={cn(
                                            "max-w-[80%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed shadow-sm",
                                            msg.role === "user"
                                                ? "bg-teal-600 text-white rounded-br-md"
                                                : msg.role === "system"
                                                    ? "bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300 border border-amber-200/60 dark:border-amber-700/30 text-xs italic"
                                                    : "bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 border border-gray-100 dark:border-white/10 rounded-bl-md"
                                        )}
                                    >
                                        <p className="whitespace-pre-wrap break-words">{msg.content}</p>
                                        <p
                                            className={cn(
                                                "text-[10px] mt-1.5 tabular-nums",
                                                msg.role === "user"
                                                    ? "text-teal-200/60"
                                                    : "text-gray-400 dark:text-gray-500"
                                            )}
                                        >
                                            {msg.timestamp.toLocaleTimeString([], {
                                                hour: "2-digit",
                                                minute: "2-digit",
                                            })}
                                        </p>
                                    </div>
                                </div>
                            ))}

                            {isTyping && (
                                <div className="flex justify-start">
                                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-teal-100 dark:bg-teal-900/40 border border-teal-200/60 dark:border-teal-700/30 mr-2 mt-0.5 shrink-0">
                                        <Bot className="h-3.5 w-3.5 text-teal-700 dark:text-teal-400" />
                                    </div>
                                    <div className="bg-white dark:bg-gray-800 border border-gray-100 dark:border-white/10 rounded-2xl rounded-bl-md px-4 py-3 shadow-sm">
                                        <div className="flex items-center gap-1.5">
                                            <span className="h-2 w-2 bg-teal-500/60 rounded-full animate-bounce [animation-delay:0ms]" />
                                            <span className="h-2 w-2 bg-teal-500/60 rounded-full animate-bounce [animation-delay:150ms]" />
                                            <span className="h-2 w-2 bg-teal-500/60 rounded-full animate-bounce [animation-delay:300ms]" />
                                        </div>
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                    <div ref={messagesEndRef} />
                </div>

                {/* Input Area */}
                <div className="border-t border-gray-100 dark:border-white/10 px-4 py-3 bg-white dark:bg-gray-900">
                    <div className="flex items-end gap-2">
                        <textarea
                            ref={inputRef}
                            value={inputValue}
                            onChange={(e) => setInputValue(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder={
                                connectionStatus === "connected"
                                    ? "Ask me anything..."
                                    : "Connecting..."
                            }
                            disabled={connectionStatus !== "connected"}
                            rows={1}
                            className={cn(
                                "flex-1 resize-none rounded-xl border bg-gray-50 dark:bg-gray-800/60 px-3.5 py-2.5 text-sm text-gray-900 dark:text-gray-100 placeholder:text-gray-400 dark:placeholder:text-gray-500 transition-colors",
                                "focus:outline-none focus:ring-2 focus:ring-teal-500/40 focus:border-teal-300 dark:focus:border-teal-600",
                                "border-gray-200 dark:border-white/10",
                                "disabled:opacity-50 disabled:cursor-not-allowed",
                                "max-h-[100px] scrollbar-thin"
                            )}
                            style={{
                                height: "auto",
                                minHeight: "40px",
                            }}
                            onInput={(e) => {
                                const target = e.target as HTMLTextAreaElement;
                                target.style.height = "auto";
                                target.style.height = Math.min(target.scrollHeight, 100) + "px";
                            }}
                        />
                        <button
                            type="button"
                            onClick={sendMessage}
                            disabled={
                                !inputValue.trim() ||
                                connectionStatus !== "connected"
                            }
                            className={cn(
                                "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-all duration-200",
                                inputValue.trim() && connectionStatus === "connected"
                                    ? "bg-teal-600 hover:bg-teal-700 text-white shadow-sm hover:shadow-md"
                                    : "bg-gray-100 dark:bg-gray-800 text-gray-400 cursor-not-allowed"
                            )}
                            aria-label="Send message"
                        >
                            {connectionStatus === "connecting" ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                                <Send className="h-4 w-4" />
                            )}
                        </button>
                    </div>
                    <p className="mt-2 text-[10px] text-gray-400 dark:text-gray-500 text-center font-medium">
                        Powered by Talky AI • Press Enter to send
                    </p>
                </div>
            </div>
        </>
    );
}
