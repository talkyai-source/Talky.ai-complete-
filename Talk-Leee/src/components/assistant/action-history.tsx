"use client";

import { useState } from "react";
import { useAssistantRuns, type AssistantRunsQuery } from "@/lib/api-hooks";
import type { AssistantRun, AssistantRunStatus } from "@/lib/models";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";

const STATUS_LABELS: Record<AssistantRunStatus, string> = {
    pending: "Pending", running: "In progress", in_progress: "In progress",
    scheduled: "Scheduled", completed: "Completed", failed: "Failed",
    unknown: "Outcome unknown", cancelled: "Cancelled",
};
const FILTER_STATUSES: AssistantRunStatus[] = ["pending", "running", "scheduled", "completed", "failed", "unknown", "cancelled"];

function formatDate(value?: string | null) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export function actionOutcome(run: AssistantRun): string {
    if (run.status === "completed" && ["send_email", "email_send"].includes(run.actionType)) {
        return run.confirmationAllowed === true ? "Accepted by email provider" : "Completion recorded — delivery unverified";
    }
    if (run.status === "completed" && ["schedule_callback", "request_callback"].includes(run.actionType)) {
        return run.receipt?.provider_status === "queued" ? "Queued for calling" : "Callback request recorded";
    }
    if (run.status === "completed" && run.confirmationAllowed !== true) return "Completion recorded — outcome unverified";
    return STATUS_LABELS[run.status];
}

function download(items: AssistantRun[], format: "json" | "csv") {
    const safe = items.map((item) => ({ id: item.id, action: item.actionType, status: item.status,
        source: item.source, created_at: item.createdAt, receipt: item.receipt ?? null }));
    // Text cells cannot become spreadsheet formulas when the audit is opened.
    const cell = (value: unknown) => {
        const raw = typeof value === "string" ? value : JSON.stringify(value ?? "");
        const literal = /^[=+@\-\t\r]/.test(raw) ? `'${raw}` : raw;
        return `"${literal.replaceAll('"', '""')}"`;
    };
    const content = format === "json" ? JSON.stringify(safe, null, 2)
        : ["id,action,status,source,created_at,receipt", ...safe.map((row) => Object.values(row).map(cell).join(","))].join("\n");
    const url = URL.createObjectURL(new Blob([content], { type: format === "json" ? "application/json" : "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `action-history-page.${format}`;
    anchor.click();
    URL.revokeObjectURL(url);
}

export function ActionHistory() {
    const [page, setPage] = useState(1);
    const [status, setStatus] = useState<AssistantRunStatus | "">("");
    const [actionType, setActionType] = useState("");
    const [from, setFrom] = useState("");
    const [to, setTo] = useState("");
    const [leadId, setLeadId] = useState<string>();
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [view, setView] = useState<"table" | "timeline">("table");
    const [selectedId, setSelectedId] = useState<string>();
    const query: AssistantRunsQuery = { page, pageSize: 50, statuses: status ? [status] : [],
        actionType: actionType.trim() || undefined, leadId, sortKey: "createdAt", sortDir,
        from: from ? `${from}T00:00:00Z` : undefined, to: to ? `${to}T23:59:59.999Z` : undefined };
    const dateError = Boolean(from && to && from > to);
    const result = useAssistantRuns(query, { enabled: !dateError });
    const items = result.data?.items ?? [];
    const selected = items.find((item) => item.id === selectedId);
    const total = result.data?.total ?? 0;
    function change(update: () => void) { update(); setPage(1); setSelectedId(undefined); }

    return <div className="space-y-5">
        <p className="text-sm text-muted-foreground">Review saved assistant actions and their provider references. To request a new action, open the Assistant and review its proposal before applying it.</p>
        <div className="rounded-xl border border-border bg-background p-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <label className="space-y-1 text-sm">Status
                    <select aria-label="Action status" className="block w-full rounded-md border border-input bg-background p-2" value={status} onChange={(event) => change(() => setStatus(event.target.value as AssistantRunStatus | ""))}>
                        <option value="">All statuses</option>
                        {FILTER_STATUSES.map((value) => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}
                    </select>
                </label>
                <label className="space-y-1 text-sm">Action type
                    <Input aria-label="Action type" value={actionType} placeholder="All types" onChange={(event) => change(() => setActionType(event.target.value))} />
                </label>
                <label className="space-y-1 text-sm">From date (UTC)
                    <Input aria-label="From date" type="date" value={from} onChange={(event) => change(() => setFrom(event.target.value))} />
                </label>
                <label className="space-y-1 text-sm">To date (UTC)
                    <Input aria-label="To date" type="date" value={to} onChange={(event) => change(() => setTo(event.target.value))} />
                </label>
            </div>
            {dateError && <p role="alert" className="mt-2 text-sm text-destructive">The end date must be on or after the start date.</p>}
            {leadId && <div className="mt-3 flex items-center gap-3 text-sm"><span>Showing this contact&apos;s actions</span><Button variant="outline" onClick={() => change(() => setLeadId(undefined))}>Clear contact filter</Button></div>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" disabled={dateError || result.isFetching} onClick={() => void result.refetch()}>Refresh</Button>
            <Button variant="outline" aria-pressed={view === "table"} onClick={() => setView("table")}>Table</Button>
            <Button variant="outline" aria-pressed={view === "timeline"} onClick={() => setView("timeline")}>Timeline</Button>
            <label className="text-sm"><span className="sr-only">Date order</span><select aria-label="Date order" className="rounded-md border border-input bg-background p-2" value={sortDir} onChange={(event) => change(() => setSortDir(event.target.value as "asc" | "desc"))}><option value="desc">Newest first</option><option value="asc">Oldest first</option></select></label>
            <Button variant="outline" disabled={dateError || !items.length || result.isPlaceholderData} onClick={() => download(items, "csv")}>Export page CSV</Button>
            <Button variant="outline" disabled={dateError || !items.length || result.isPlaceholderData} onClick={() => download(items, "json")}>Export page JSON</Button>
        </div>
        {result.isError && <div role="alert" className="rounded-lg border border-destructive/30 p-3 text-sm">{result.error instanceof Error ? result.error.message : "Action history could not be loaded."} Refresh to check saved records; a failed history request does not tell us whether an action ran.</div>}
        {!dateError && result.isPending && <p role="status">Loading action history…</p>}
        {result.isPlaceholderData && <p role="status">Updating results…</p>}
        {!dateError && !result.isPending && !result.isError && !items.length && <p className="rounded-lg border border-border p-5 text-sm text-muted-foreground">No saved actions match these filters.</p>}
        {!dateError && items.length > 0 && (view === "table" ? <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full text-left text-sm" aria-label="Saved assistant actions"><thead className="bg-muted"><tr><th className="p-3">Action</th><th className="p-3">Outcome</th><th className="p-3">Created</th><th className="p-3">Details</th></tr></thead>
                <tbody>{items.map((item) => <tr key={item.id} className="border-t border-border"><td className="p-3">{item.actionType.replaceAll("_", " ")}</td><td className="p-3">{actionOutcome(item)}</td><td className="p-3">{formatDate(item.createdAt)}</td><td className="p-3"><Button variant="outline" onClick={() => setSelectedId(item.id)}>View record</Button></td></tr>)}</tbody>
            </table>
        </div> : <ol aria-label="Saved assistant actions" className="space-y-3">{items.map((item) => <li key={item.id} className="rounded-xl border border-border p-4"><p className="font-medium">{item.actionType.replaceAll("_", " ")}</p><p className="my-2 text-sm">{actionOutcome(item)} · {formatDate(item.createdAt)}</p><Button variant="outline" onClick={() => setSelectedId(item.id)}>View record</Button></li>)}</ol>)}
        {result.data && !result.isError && !dateError && <div className="flex items-center justify-between gap-3 text-sm"><span>{total} records · Page {page}</span><div className="flex gap-2"><Button variant="outline" disabled={page === 1 || result.isFetching} onClick={() => { setSelectedId(undefined); setPage(page - 1); }}>Previous</Button><Button variant="outline" disabled={page * 50 >= total || result.isFetching} onClick={() => { setSelectedId(undefined); setPage(page + 1); }}>Next</Button></div></div>}
        <Modal open={Boolean(selected)} onOpenChange={(open) => { if (!open) setSelectedId(undefined); }} title="Action record">
            {selected && <div className="space-y-3 text-sm">
                <p className="font-medium">{actionOutcome(selected)}</p>
                {(["unknown", "running", "in_progress", "pending"] as AssistantRunStatus[]).includes(selected.status) && <p>Do not resend this action while its result is unresolved. Ask your workspace administrator to review this reference and the original provider account.</p>}
                {selected.status === "scheduled" && <p>The request is scheduled. Check its saved outcome after execution.</p>}
                {selected.status === "completed" && ["schedule_callback", "request_callback"].includes(selected.actionType) && <p>This record confirms the callback request or queue handoff. Check call history for the actual call outcome.</p>}
                {selected.status === "failed" && <p>Review the saved result with your administrator before requesting another action.</p>}
                <dl className="space-y-2 break-all"><dt className="font-medium">Action reference</dt><dd>{selected.id}</dd><dt className="font-medium">Action</dt><dd>{selected.actionType.replaceAll("_", " ")}</dd><dt className="font-medium">Source</dt><dd>{selected.source}</dd><dt className="font-medium">Created</dt><dd>{formatDate(selected.createdAt)}</dd>
                    {selected.receipt && Object.entries(selected.receipt).map(([key, value]) => <div key={key}><dt className="font-medium">{key.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}
                </dl>
                {typeof selected.error === "string" && <p role="status">{selected.error}</p>}
                {selected.leadId && <Button variant="outline" onClick={() => change(() => setLeadId(selected.leadId ?? undefined))}>View this contact&apos;s actions</Button>}
            </div>}
        </Modal>
    </div>;
}
