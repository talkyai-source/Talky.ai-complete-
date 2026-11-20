import assert from "node:assert/strict";
import test from "node:test";
import type { Call } from "@/lib/dashboard-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import { exportCallHistory } from "./index";
import { loadLogoPng } from "./logo";

const call: Call = {
    id: "abcdef12-0000-0000-0000-000000000000", campaign_id: "c", campaign_name: "Spring", lead_id: "l", phone_number: "+447429916656",
    status: "completed", outcome: "goal_achieved", duration_seconds: 90, created_at: "2026-11-21T09:00:00Z",
};
const entry = (notes: string): CallHistoryWorkflowEntry => ({
    leadType: "warm", notes, form: { contact: "", interest: "", nextStep: "", completed: false }, updatedAt: "x",
});
const meta = { accountName: null, timeZone: "UTC", filters: [], complete: true };

test("knownWorkflow wins over what the server returned; the file is saved once with the right name", async () => {
    const saves: Array<{ bytes: Uint8Array; name: string; mime: string }> = [];
    const result = await exportCallHistory(
        { format: "pdf", calls: [call], knownWorkflow: { [call.id]: entry("FROM PAGE") }, meta },
        {
            fetchWorkflow: async () => ({ [call.id]: entry("FROM SERVER") }),
            save: (bytes, name, mime) => { saves.push({ bytes, name, mime }); },
            loadLogo: async () => null,
        },
    );
    assert.equal(result.calls, 1);
    assert.match(result.fileName, /^talk-lee-call-history-\d{4}-\d\d-\d\d\.pdf$/);
    assert.equal(saves.length, 1);
    assert.equal(saves[0].name, result.fileName);
    assert.equal(saves[0].mime, "application/pdf");
    assert.equal(Buffer.from(saves[0].bytes).toString("latin1").slice(0, 4), "%PDF");
});

test("the notes in the Excel file are the page's, not the server's, when both exist", async () => {
    const ExcelJS = (await import("exceljs")).default;
    let saved: Uint8Array | null = null;
    await exportCallHistory(
        { format: "xlsx", calls: [call], knownWorkflow: { [call.id]: entry("FROM PAGE") }, meta },
        { fetchWorkflow: async () => ({ [call.id]: entry("FROM SERVER") }), save: (b) => { saved = b; }, loadLogo: async () => null },
    );
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load(saved! as unknown as ArrayBuffer);
    const values: string[] = [];
    wb.getWorksheet("Call history")!.eachRow((row) => row.eachCell((cell) => values.push(String(cell.value))));
    assert.ok(values.includes("FROM PAGE"));
    assert.ok(!values.includes("FROM SERVER"));
});

test("a server note is used when the page holds none", async () => {
    const ExcelJS = (await import("exceljs")).default;
    let saved: Uint8Array | null = null;
    await exportCallHistory(
        { format: "xlsx", calls: [call], meta },
        { fetchWorkflow: async () => ({ [call.id]: entry("FROM SERVER") }), save: (b) => { saved = b; }, loadLogo: async () => null },
    );
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load(saved! as unknown as ArrayBuffer);
    const values: string[] = [];
    wb.getWorksheet("Call history")!.eachRow((row) => row.eachCell((cell) => values.push(String(cell.value))));
    assert.ok(values.includes("FROM SERVER"));
});

test("an aborted signal rejects with AbortError and saves nothing", async () => {
    const controller = new AbortController();
    controller.abort();
    let saves = 0;
    await assert.rejects(
        exportCallHistory(
            { format: "xlsx", calls: [call], meta, signal: controller.signal },
            { fetchWorkflow: async () => ({}), save: () => { saves += 1; }, loadLogo: async () => null },
        ),
        (err: unknown) => err instanceof DOMException && err.name === "AbortError",
    );
    assert.equal(saves, 0);
});

test("aborting while the notes load also saves nothing", async () => {
    const controller = new AbortController();
    let saves = 0;
    await assert.rejects(
        exportCallHistory(
            { format: "pdf", calls: [call], meta, signal: controller.signal },
            {
                fetchWorkflow: async () => { controller.abort(); return {}; },
                save: () => { saves += 1; },
                loadLogo: async () => null,
            },
        ),
        (err: unknown) => err instanceof DOMException && err.name === "AbortError",
    );
    assert.equal(saves, 0);
});

test("exportCallHistory hands the brand to the signature loader", async () => {
    let seen: unknown = null;
    const brand = { name: "Partner Co", logoSrc: "/partner.svg" };
    await exportCallHistory(
        { format: "pdf", calls: [call], meta, brand },
        { fetchWorkflow: async () => ({}), save: () => undefined, loadSignature: async (b) => { seen = b; return null; } },
    );
    assert.deepEqual(seen, brand);
});

test("loadLogoPng returns null when there is no document", async () => {
    const g = globalThis as unknown as Record<string, unknown>;
    const original = Object.getOwnPropertyDescriptor(globalThis, "document");
    Object.defineProperty(globalThis, "document", { value: undefined, configurable: true, writable: true });
    try {
        assert.equal(await loadLogoPng("/favicon.svg"), null);
    } finally {
        if (original) Object.defineProperty(globalThis, "document", original);
        else delete g.document;
    }
});
