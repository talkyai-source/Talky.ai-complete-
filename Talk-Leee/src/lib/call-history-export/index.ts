import type { Call } from "@/lib/dashboard-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import {
    DEFAULT_BRAND,
    buildExportRows,
    exportFileName,
    type CallExportBrand,
    type CallExportFormat,
    type CallExportMeta,
    type CallExportProgress,
    type CallExportRow,
    type CallExportLogo,
} from "./columns";
import { fetchWorkflowForCalls } from "./notes";
import { loadLogoPng } from "./logo";
import { buildCallHistoryWorkbook } from "./xlsx";
import { buildCallHistoryPdf } from "./pdf";
import { saveBytes } from "./download";

export type {
    CallExportBrand,
    CallExportFormat,
    CallExportLogo,
    CallExportMeta,
    CallExportProgress,
    CallExportRow,
} from "./columns";
export { CALL_EXPORT_COLUMNS, buildExportRows, summarizeExport, exportFileName } from "./columns";
export { fetchWorkflowForCalls } from "./notes";
export { loadLogoPng } from "./logo";
export { buildCallHistoryWorkbook } from "./xlsx";
export { buildCallHistoryPdf } from "./pdf";
export { buildSingleCallPdf, type SingleCallExport } from "./single-call-pdf";
export { saveBytes } from "./download";

const MIME: Record<CallExportFormat, string> = {
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    pdf: "application/pdf",
};

/** Test seams; production callers never pass these. */
export interface ExportInternals {
    fetchWorkflow?: typeof fetchWorkflowForCalls;
    save?: typeof saveBytes;
    loadLogo?: (src: string) => Promise<CallExportLogo | null>;
}

export async function exportCallHistory(
    args: {
        format: CallExportFormat;
        calls: Call[];
        knownWorkflow?: Record<string, CallHistoryWorkflowEntry | undefined>;
        meta: Omit<CallExportMeta, "generatedAt">;
        brand?: CallExportBrand;
        signal?: AbortSignal;
        onProgress?: (p: CallExportProgress) => void;
    },
    internals: ExportInternals = {},
): Promise<{ fileName: string; calls: number }> {
    const { format, calls, knownWorkflow, signal, onProgress } = args;
    const brand = args.brand ?? DEFAULT_BRAND;
    const fetchWorkflow = internals.fetchWorkflow ?? fetchWorkflowForCalls;
    const save = internals.save ?? saveBytes;
    const loadLogo = internals.loadLogo ?? ((src: string) => loadLogoPng(src));
    const check = () => {
        if (signal?.aborted) throw new DOMException("The export was cancelled.", "AbortError");
    };

    check();
    const fetched = await fetchWorkflow(calls.map((call) => call.id), { signal, onProgress });
    const workflow: Record<string, CallHistoryWorkflowEntry | undefined> = { ...fetched };
    for (const [id, entry] of Object.entries(knownWorkflow ?? {})) {
        if (entry) workflow[id] = entry;
    }
    check();
    onProgress?.({ phase: "file", done: 0, total: 1 });
    const rows: CallExportRow[] = buildExportRows(calls, workflow, args.meta.timeZone);
    const logo = await loadLogo(brand.logoSrc);
    check();
    const meta: CallExportMeta = { ...args.meta, generatedAt: new Date() };
    const bytes = format === "xlsx"
        ? await buildCallHistoryWorkbook(rows, meta, brand, logo)
        : await buildCallHistoryPdf(rows, meta, brand, logo);
    check();
    const fileName = exportFileName(format, meta.generatedAt);
    save(bytes, fileName, MIME[format]);
    onProgress?.({ phase: "file", done: 1, total: 1 });
    return { fileName, calls: rows.length };
}
