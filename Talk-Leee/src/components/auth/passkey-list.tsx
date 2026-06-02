"use client";

import { useCallback, useEffect, useState } from "react";
import { Key, Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

type Passkey = {
    id: string;
    credential_id: string;
    display_name: string;
    device_type: string;
    backed_up: boolean;
    transports: string[];
    created_at: string;
    last_used_at?: string | null;
};

export type PasskeyListProps = {
    // Bumping this value forces a re-fetch — used by the parent after a
    // successful registration so the new passkey appears without a full
    // page reload.
    refreshKey?: number;
};

function formatDate(value?: string): string {
    if (!value) return "—";
    try {
        return new Date(value).toLocaleString();
    } catch {
        return value;
    }
}

export default function PasskeyList({ refreshKey = 0 }: PasskeyListProps) {
    const [passkeys, setPasskeys] = useState<Passkey[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [deletingId, setDeletingId] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const items = await api.listPasskeys();
            setPasskeys(items);
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load passkeys");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load, refreshKey]);

    async function handleDelete(passkey: Passkey) {
        const confirmed = window.confirm(
            `Remove passkey "${passkey.display_name || passkey.device_type}"?`,
        );
        if (!confirmed) return;
        setDeletingId(passkey.id);
        try {
            await api.deletePasskey(passkey.id);
            setPasskeys((prev) => prev.filter((p) => p.id !== passkey.id));
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to delete passkey");
        } finally {
            setDeletingId(null);
        }
    }

    if (loading) {
        return (
            <div className="flex items-center gap-2 text-sm text-muted-foreground py-2">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                Loading passkeys…
            </div>
        );
    }

    if (error) {
        return (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
                {error}
            </div>
        );
    }

    if (passkeys.length === 0) {
        return (
            <p className="text-sm text-muted-foreground py-1">
                No passkeys registered yet.
            </p>
        );
    }

    return (
        <ul className="divide-y divide-gray-200 dark:divide-white/10 rounded-md border border-gray-200 dark:border-white/10 overflow-hidden">
            {passkeys.map((pk) => (
                <li
                    key={pk.id}
                    className="flex items-center justify-between gap-3 px-3 py-3 text-sm bg-white dark:bg-white/5"
                >
                    <div className="flex items-start gap-2 min-w-0">
                        <Key className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" aria-hidden />
                        <div className="min-w-0">
                            <div className="font-medium text-gray-900 dark:text-zinc-100 truncate">
                                {pk.display_name || pk.device_type || "Passkey"}
                            </div>
                            <div className="text-xs text-muted-foreground">
                                Added {formatDate(pk.created_at)}
                                {pk.last_used_at ? ` · Last used ${formatDate(pk.last_used_at)}` : ""}
                                {pk.backed_up ? " · Synced" : ""}
                            </div>
                        </div>
                    </div>
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(pk)}
                        disabled={deletingId === pk.id}
                        aria-label={`Remove passkey ${pk.display_name || pk.device_type}`}
                    >
                        {deletingId === pk.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                        ) : (
                            <Trash2 className="h-4 w-4 text-red-600" aria-hidden />
                        )}
                    </Button>
                </li>
            ))}
        </ul>
    );
}
