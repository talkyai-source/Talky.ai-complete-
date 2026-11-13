"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { sharedHttpClient } from "@/lib/api";
import type { Call } from "@/lib/dashboard-api";
import {
    CALL_HISTORY_LEAD_TYPES,
    defaultCallHistoryWorkflow,
    readCallHistoryWorkflow,
    type CallHistoryWorkflowEntry,
    type CallHistoryWorkflowMap,
} from "@/lib/call-history-workflow";

const valuesSchema = z.object({
    leadType: z.enum(CALL_HISTORY_LEAD_TYPES),
    notes: z.string().max(4000),
    form: z.object({
        contact: z.string().max(160),
        interest: z.string().max(1000),
        nextStep: z.string().max(1000),
        completed: z.boolean(),
    }),
});
const entrySchema = valuesSchema.extend({ updatedAt: z.string() });
const responseSchema = z.object({ workflow: entrySchema.nullable() });
export type CallWorkflowPatch = Partial<Omit<CallHistoryWorkflowEntry, "updatedAt">>;
type View = { scope: string; workflow: CallHistoryWorkflowMap; busy: boolean; failed: boolean; backupUnavailable: boolean };
type Item = {
    call: Call;
    base: CallHistoryWorkflowEntry;
    loaded: boolean;
    pending: CallWorkflowPatch;
    paused: boolean;
    failed: boolean;
    importLegacy: boolean;
    busy: boolean;
    queued: boolean;
    timer?: ReturnType<typeof setTimeout>;
};

function values(entry: CallHistoryWorkflowEntry) {
    return { leadType: entry.leadType, notes: entry.notes, form: entry.form };
}

// This session belongs to one tenant AND actor. It never rewrites the original
// v1 browser backup. Confirmed imports and unacknowledged edits are separate:
// reopening a failed edit must not silently replace a colleague's newer row.
class WorkflowSession {
    private active = true;
    private abort = new AbortController();
    private items = new Map<string, Item>();
    private queue: Item[] = [];
    private running = 0;
    private legacy: CallHistoryWorkflowMap;
    private drafts: Record<string, CallWorkflowPatch> = {};
    private imported: Record<string, boolean> = {};
    private pendingKey: string;
    private importedKey: string;
    private backupUnavailable = false;
    private unreadableBackup = false;

    constructor(private scope: string, tenant: string, private notify: (view: View) => void) {
        this.pendingKey = `talklee.call-history.pending.v1:${encodeURIComponent(scope)}`;
        this.importedKey = `talklee.call-history.imported.v1:${encodeURIComponent(tenant)}`;
        this.legacy = readCallHistoryWorkflow(tenant);
        try {
            this.drafts = z.record(z.string(), valuesSchema.partial()).parse(JSON.parse(localStorage.getItem(this.pendingKey) ?? "{}"));
            this.imported = z.record(z.string(), z.boolean()).parse(JSON.parse(localStorage.getItem(this.importedKey) ?? "{}"));
        } catch {
            // Preserve unreadable backup bytes instead of replacing them with
            // an empty map. Server reads/writes can still work this session.
            this.backupUnavailable = this.unreadableBackup = true;
        }
    }

    private emit() {
        if (!this.active) return;
        const workflow: CallHistoryWorkflowMap = {};
        for (const [id, item] of this.items) workflow[id] = { ...item.base, ...item.pending };
        this.notify({
            scope: this.scope,
            workflow,
            busy: [...this.items.values()].some((item) => item.busy || item.queued || Boolean(item.timer)),
            failed: [...this.items.values()].some((item) => item.failed),
            backupUnavailable: this.backupUnavailable,
        });
    }

    private backup() {
        if (this.unreadableBackup) return;
        try {
            localStorage.setItem(this.pendingKey, JSON.stringify(this.drafts));
            localStorage.setItem(this.importedKey, JSON.stringify(this.imported));
            this.backupUnavailable = false;
        } catch {
            this.backupUnavailable = true;
        }
    }

    ensure(calls: Call[]) {
        for (const call of calls) {
            if (this.items.has(call.id)) continue;
            const pending = this.drafts[call.id] ?? {};
            const restored = Object.keys(pending).length > 0;
            const item: Item = {
                call, base: this.legacy[call.id] ?? defaultCallHistoryWorkflow(call),
                loaded: false, pending, paused: restored, failed: restored,
                importLegacy: Boolean(this.legacy[call.id]) && !this.imported[call.id],
                busy: false, queued: false,
            };
            this.items.set(call.id, item);
            this.enqueue(item);
        }
        this.emit();
    }

    change(call: Call, patch: CallWorkflowPatch) {
        if (!this.active) return;
        this.ensure([call]);
        const item = this.items.get(call.id)!;
        item.pending = { ...item.pending, ...patch };
        this.drafts[call.id] = item.pending;
        this.backup();
        if (item.timer) clearTimeout(item.timer);
        if (!item.paused && !item.busy) {
            item.timer = setTimeout(() => {
                item.timer = undefined;
                this.enqueue(item);
            }, 250);
        }
        this.emit();
    }

    retry() {
        for (const item of this.items.values()) {
            if (!item.failed || item.busy) continue;
            item.failed = item.paused = false;
            // Refresh unrelated fields before retrying a patch, including when
            // the previous PUT may have committed but its response was lost.
            item.loaded = false;
            this.enqueue(item);
        }
        this.emit();
    }

    private enqueue(item: Item) {
        if (!this.active || item.busy || item.queued) return;
        item.queued = true;
        this.queue.push(item);
        this.pump();
    }

    private pump() {
        while (this.active && this.running < 4 && this.queue.length) {
            const item = this.queue.shift()!;
            item.queued = false;
            item.busy = true;
            this.running += 1;
            void this.sync(item);
        }
        this.emit();
    }

    private async request(item: Item, body?: ReturnType<typeof values> & { create_only?: boolean }) {
        const data = await sharedHttpClient().request({
            path: `/calls/${encodeURIComponent(item.call.id)}/workflow`,
            method: body ? "PUT" : "GET",
            ...(body ? { body } : {}),
            signal: this.abort.signal,
            ownsOperation: () => this.active,
        });
        const workflow = responseSchema.parse(data).workflow;
        if (body && !workflow) throw new Error("Missing saved workflow");
        return workflow;
    }

    private async sync(item: Item) {
        try {
            if (!item.loaded) {
                const workflow = await this.request(item);
                if (!this.active) return;
                item.base = workflow ?? (item.importLegacy ? this.legacy[item.call.id] : defaultCallHistoryWorkflow(item.call));
                item.loaded = true;
                this.emit();
            }
            if (item.importLegacy) {
                const workflow = await this.request(item, { ...values(this.legacy[item.call.id]), create_only: true });
                if (!this.active) return;
                item.base = workflow!;
                item.importLegacy = false;
                this.imported[item.call.id] = true;
                this.backup();
                this.emit();
            }
            if (!item.paused && Object.keys(item.pending).length) {
                const submitted = item.pending;
                const workflow = await this.request(item, values({ ...item.base, ...submitted }));
                if (!this.active) return;
                item.base = workflow!;
                const remaining = { ...item.pending };
                // A response only acknowledges its own fields/values. Edits
                // made during that PUT remain visible and are sent next.
                for (const key of Object.keys(submitted) as Array<keyof CallWorkflowPatch>) {
                    if (remaining[key] === submitted[key]) delete remaining[key];
                }
                item.pending = remaining;
                if (Object.keys(remaining).length) this.drafts[item.call.id] = remaining;
                else delete this.drafts[item.call.id];
                this.backup();
            }
        } catch {
            if (this.active) item.failed = item.paused = true;
        } finally {
            item.busy = false;
            this.running -= 1;
            if (this.active && !item.paused && Object.keys(item.pending).length) this.enqueue(item);
            this.pump();
        }
    }

    dispose() {
        this.active = false;
        this.abort.abort();
        this.queue = [];
        for (const item of this.items.values()) if (item.timer) clearTimeout(item.timer);
    }
}

const EMPTY: View = { scope: "", workflow: {}, busy: false, failed: false, backupUnavailable: false };

export function useCallHistoryWorkflow(calls: Call[], tenant?: string | null, actor?: string | null) {
    const scope = tenant && actor ? `${tenant}:${actor}` : "";
    const session = useRef<WorkflowSession | null>(null);
    const [view, setView] = useState<View>(EMPTY);

    useEffect(() => {
        if (!scope || !tenant) return;
        const current = new WorkflowSession(scope, tenant, setView);
        session.current = current;
        return () => {
            current.dispose();
            session.current = null;
        };
    }, [scope, tenant]);

    useEffect(() => {
        session.current?.ensure(calls);
    }, [calls, scope]);

    const update = useCallback((call: Call, patch: CallWorkflowPatch) => session.current?.change(call, patch), []);
    const retry = useCallback(() => session.current?.retry(), []);
    return { ...(view.scope === scope ? view : EMPTY), update, retry };
}
