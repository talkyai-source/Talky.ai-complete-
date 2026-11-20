import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import { CALL_HISTORY_LEAD_TYPES } from "@/lib/call-history-workflow";
import type { CallExportProgress } from "./columns";

export interface WorkflowHttpClient {
    request(opts: {
        path: string;
        method?: "GET" | "POST";
        body?: unknown;
        signal?: AbortSignal;
    }): Promise<unknown>;
}

const BATCH_SIZE = 200;
const MAX_IN_FLIGHT = 6;

function abortError(): DOMException {
    return new DOMException("The export was cancelled.", "AbortError");
}

function statusOf(err: unknown): number | undefined {
    const status = (err as { status?: unknown } | null)?.status;
    return typeof status === "number" ? status : undefined;
}

function parseEntry(value: unknown): CallHistoryWorkflowEntry | undefined {
    if (!value || typeof value !== "object") return undefined;
    const raw = value as Record<string, unknown>;
    const leadType = (CALL_HISTORY_LEAD_TYPES as readonly string[]).includes(raw.leadType as string)
        ? (raw.leadType as CallHistoryWorkflowEntry["leadType"])
        : undefined;
    if (!leadType) return undefined;
    const form = (raw.form && typeof raw.form === "object" ? raw.form : {}) as Record<string, unknown>;
    const text = (v: unknown) => (typeof v === "string" ? v : "");
    return {
        leadType,
        notes: text(raw.notes),
        form: {
            contact: text(form.contact),
            interest: text(form.interest),
            nextStep: text(form.nextStep),
            completed: form.completed === true,
        },
        updatedAt: text(raw.updatedAt),
    };
}

export async function fetchWorkflowForCalls(
    callIds: string[],
    opts: { signal?: AbortSignal; onProgress?: (p: CallExportProgress) => void } = {},
    client?: WorkflowHttpClient,
): Promise<Record<string, CallHistoryWorkflowEntry | undefined>> {
    const http: WorkflowHttpClient = client ?? ((await import("@/lib/api")).sharedHttpClient() as WorkflowHttpClient);
    const ids = [...new Set(callIds)];
    const total = ids.length;
    const result: Record<string, CallHistoryWorkflowEntry | undefined> = {};
    let done = 0;
    const report = () => opts.onProgress?.({ phase: "notes", done, total });
    const check = () => {
        if (opts.signal?.aborted) throw abortError();
    };
    report();

    let fallback = false;
    let at = 0;
    while (at < ids.length && !fallback) {
        check();
        const chunk = ids.slice(at, at + BATCH_SIZE);
        try {
            const data = await http.request({
                path: "/calls/workflow/batch",
                method: "POST",
                body: { call_ids: chunk },
                signal: opts.signal,
            });
            const items = ((data as { items?: unknown } | null)?.items ?? {}) as Record<string, unknown>;
            for (const id of chunk) {
                const entry = parseEntry(items[id]);
                if (entry) result[id] = entry;
            }
            at += chunk.length;
            done = at;
            report();
        } catch (err) {
            const status = statusOf(err);
            if (status === 404 || status === 405) fallback = true;
            else throw err;
        }
    }

    if (fallback) {
        const rest = ids.slice(at);
        let next = 0;
        const worker = async () => {
            while (next < rest.length) {
                check();
                const id = rest[next++];
                try {
                    const data = await http.request({
                        path: `/calls/${encodeURIComponent(id)}/workflow`,
                        method: "GET",
                        signal: opts.signal,
                    });
                    const entry = parseEntry((data as { workflow?: unknown } | null)?.workflow);
                    if (entry) result[id] = entry;
                } catch (err) {
                    if (statusOf(err) !== 404) throw err;
                }
                done += 1;
                report();
            }
        };
        await Promise.all(Array.from({ length: Math.min(MAX_IN_FLIGHT, rest.length) }, worker));
    }
    check();
    return result;
}
