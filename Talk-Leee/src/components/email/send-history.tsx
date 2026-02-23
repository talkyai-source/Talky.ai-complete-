"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { useEmailAuditActions, useEmailAuditState } from "@/lib/email-audit-client";
import { cn } from "@/lib/utils";

function statusClass(status: string) {
    if (status === "success") return "bg-emerald-500/10 text-emerald-200 border-emerald-500/20";
    if (status === "failed") return "bg-red-500/10 text-red-200 border-red-500/20";
    return "bg-white/5 text-gray-200 border-white/10";
}

export function SendHistory({ className }: { className?: string }) {
    const { items } = useEmailAuditState();
    const { clearAll, exportHistoryJson } = useEmailAuditActions();
    const [filter, setFilter] = useState<"all" | "success" | "failed" | "pending">("all");

    const filtered = useMemo(() => {
        if (filter === "all") return items;
        return items.filter((i) => i.status === filter);
    }, [filter, items]);

    return (
        <div className={cn("rounded-2xl border border-white/10 bg-white/5 p-4", className)}>
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="text-sm font-semibold text-white">Send history</div>
                    <div className="mt-1 text-xs text-gray-300">Includes successful and failed send attempts.</div>
                </div>
                <div className="flex flex-wrap gap-2">
                    <select
                        value={filter}
                        onChange={(e) => setFilter(e.target.value as typeof filter)}
                        className="h-9 rounded-md border border-white/10 bg-white/5 px-2 text-sm text-white"
                        aria-label="Filter history"
                    >
                        <option value="all">All</option>
                        <option value="success">Success</option>
                        <option value="failed">Failed</option>
                        <option value="pending">Pending</option>
                    </select>
                    <Button
                        type="button"
                        variant="secondary"
                        onClick={() => {
                            const payload = exportHistoryJson();
                            navigator.clipboard.writeText(payload).catch(() => {});
                        }}
                    >
                        Copy JSON
                    </Button>
                    <Button type="button" variant="ghost" onClick={() => clearAll()}>
                        Clear
                    </Button>
                </div>
            </div>

            <div className="mt-4">
                {filtered.length === 0 ? (
                    <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-sm text-gray-300">No entries yet.</div>
                ) : (
                    <div className="max-h-[360px] overflow-y-auto rounded-xl border border-white/10">
                        <div className="divide-y divide-white/10">
                            {filtered.map((e) => (
                                <div key={e.id} className="px-4 py-3">
                                    <div className="flex flex-wrap items-center justify-between gap-2">
                                        <div className="min-w-0">
                                            <div className="truncate text-sm font-semibold text-white">{e.subject ?? "Untitled email"}</div>
                                            <div className="mt-1 text-xs text-gray-300">
                                                {new Date(e.createdAt).toLocaleString()} • {e.to.join(", ")}
                                            </div>
                                        </div>
                                        <div className={cn("inline-flex items-center rounded-full border px-3 py-1 text-xs font-bold", statusClass(e.status))}>
                                            {e.status}
                                        </div>
                                    </div>
                                    {e.messageId ? <div className="mt-2 text-xs text-gray-300">Message ID: {e.messageId}</div> : null}
                                    {e.providerStatus ? <div className="mt-1 text-xs text-gray-300">Status: {e.providerStatus}</div> : null}
                                    {e.errorMessage ? <div className="mt-2 text-xs text-red-200">Error: {e.errorMessage}</div> : null}
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

