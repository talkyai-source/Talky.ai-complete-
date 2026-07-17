"use client";

/*
 * Conversation history panel for the floating assistant.
 *
 * Lists past assistant conversations (GET /assistant/conversations), opens
 * one (GET /assistant/conversations/{id} → parsed message array handed back
 * to the parent, which rehydrates the chat and re-binds the WS to that
 * conversation), and deletes (DELETE /assistant/conversations/{id}).
 * Rendered in place of the message list when History is toggled — the same
 * body-swap pattern voice mode uses.
 */

import { Loader2, MessageSquare, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

export interface StoredMessage {
    role?: string;
    content?: string;
    timestamp?: string;
}

interface ConversationRow {
    id: string;
    title?: string | null;
    message_count?: number | null;
    last_message_at?: string | null;
}

function timeAgo(iso?: string | null): string {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return "";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
}

export function ConversationHistory({
    activeId,
    onOpen,
}: {
    activeId: string | null;
    onOpen: (id: string, messages: StoredMessage[]) => void;
}) {
    const [rows, setRows] = useState<ConversationRow[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busyId, setBusyId] = useState<string | null>(null);

    const load = useCallback(async () => {
        setError(null);
        try {
            const data = await api.request<{ conversations?: ConversationRow[] }>({
                path: "/assistant/conversations?page=1&page_size=30",
                method: "GET",
            });
            setRows(Array.isArray(data?.conversations) ? data.conversations : []);
        } catch {
            setError("Couldn't load your conversations. Please try again.");
            setRows([]);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const open = useCallback(
        async (id: string) => {
            setBusyId(id);
            setError(null);
            try {
                const data = await api.request<{ id: string; messages?: unknown }>({
                    path: `/assistant/conversations/${encodeURIComponent(id)}`,
                    method: "GET",
                });
                let msgs: unknown = data?.messages ?? [];
                // The backend stores messages as a JSON string (jsonb text
                // binding); older rows may already be arrays. Accept both.
                if (typeof msgs === "string") {
                    try {
                        msgs = JSON.parse(msgs);
                    } catch {
                        msgs = [];
                    }
                }
                onOpen(id, Array.isArray(msgs) ? (msgs as StoredMessage[]) : []);
            } catch {
                setError("Couldn't open that conversation.");
            } finally {
                setBusyId(null);
            }
        },
        [onOpen],
    );

    const remove = useCallback(async (id: string) => {
        setBusyId(id);
        setError(null);
        try {
            await api.request({
                path: `/assistant/conversations/${encodeURIComponent(id)}`,
                method: "DELETE",
            });
            setRows((rs) => (rs ?? []).filter((r) => r.id !== id));
        } catch {
            setError("Couldn't delete that conversation.");
        } finally {
            setBusyId(null);
        }
    }, []);

    if (rows === null) {
        return (
            <div className="flex flex-1 items-center justify-center gap-2 px-3 py-6 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Loading conversations…
            </div>
        );
    }

    return (
        <div className="flex-1 space-y-1.5 overflow-y-auto px-3 py-3">
            {error && <p className="px-1 text-xs text-red-500">{error}</p>}
            {rows.length === 0 && !error && (
                <p className="px-1 py-4 text-center text-xs text-muted-foreground">
                    No conversations yet — say hi and one will appear here.
                </p>
            )}
            {rows.map((r) => (
                <div
                    key={r.id}
                    className={`group flex items-center gap-2 rounded-lg border px-2.5 py-2 transition-colors ${
                        r.id === activeId
                            ? "border-cyan-500/40 bg-cyan-500/10"
                            : "border-border hover:bg-muted"
                    }`}
                >
                    <button
                        type="button"
                        onClick={() => void open(r.id)}
                        disabled={busyId === r.id}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    >
                        <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1">
                            <span className="block truncate text-xs font-medium text-foreground">
                                {r.title || "Conversation"}
                            </span>
                            <span className="block text-[10px] text-muted-foreground">
                                {timeAgo(r.last_message_at)}
                                {typeof r.message_count === "number"
                                    ? ` · ${r.message_count} messages`
                                    : ""}
                            </span>
                        </span>
                        {busyId === r.id && (
                            <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
                        )}
                    </button>
                    <button
                        type="button"
                        onClick={() => void remove(r.id)}
                        disabled={busyId === r.id}
                        aria-label="Delete conversation"
                        title="Delete conversation"
                        className="rounded-md p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-muted hover:text-red-500 group-hover:opacity-100"
                    >
                        <Trash2 className="h-3.5 w-3.5" />
                    </button>
                </div>
            ))}
        </div>
    );
}
