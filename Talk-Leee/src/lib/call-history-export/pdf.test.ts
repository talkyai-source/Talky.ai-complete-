import assert from "node:assert/strict";
import test from "node:test";
import type { Call } from "@/lib/dashboard-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import { buildExportRows, NOT_COMPLETE_SENTENCE, type CallExportMeta } from "./columns";
import { buildCallHistoryPdf, pdfSafeText } from "./pdf";

const brand = { name: "Talk-Lee", logoSrc: "/favicon.svg" };
const LOGO = {
    dataUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
    width: 1,
    height: 1,
};
const meta = (over: Partial<CallExportMeta> = {}): CallExportMeta => ({
    accountName: "Acme Ltd",
    timeZone: "UTC",
    filters: ["Direction: incoming"],
    generatedAt: new Date("2026-11-21T10:00:00Z"),
    complete: true,
    ...over,
});

function makeCall(i: number, over: Partial<Call> = {}): Call {
    return {
        id: `call-${String(i).padStart(4, "0")}-aaaa-bbbb`, campaign_id: "camp-1", campaign_name: `Campaign ${i % 3}`, lead_id: "lead-1",
        phone_number: "+447429916656", status: "completed", outcome: "goal_achieved", duration_seconds: 60 + i,
        created_at: "2026-11-21T09:00:00Z", summary: "Wants a quote next week and asked about annual pricing and onboarding.",
        recording_id: "rec-1", lead_outcome: "qualified | pricing", best_time_to_call: "Mornings",
        captured_phone: "+447429916656", captured_email: `person${i}@example.com`, captured_phone_confirmed: true,
        captured_email_confirmed: true, has_feedback: false, direction: "outbound", from_number: "+442046132300",
        to_number: "+447429916656", ...over,
    };
}

const text = (bytes: Uint8Array) => Buffer.from(bytes).toString("latin1");
const pageCount = (bytes: Uint8Array) => (text(bytes).match(/\/Type\s*\/Page(?!s)/g) ?? []).length;

test("a 30-row list PDF has several pages, a header, the footer 'Page X of Y' and the summary line", async () => {
    const calls = Array.from({ length: 30 }, (_, i) => makeCall(i));
    const rows = buildExportRows(calls, {}, "UTC");
    // compress:false so the page text is searchable in the raw bytes (real downloads are compressed)
    const bytes = await buildCallHistoryPdf(rows, meta(), brand, LOGO, { compress: false });
    const raw = text(bytes);
    assert.equal(raw.slice(0, 4), "%PDF");
    const pages = pageCount(bytes);
    assert.ok(pages > 1, `pages=${pages}`);
    assert.ok(raw.includes("Page 1 of " + pages));
    assert.ok(raw.includes(`Page ${pages} of ${pages}`));
    assert.ok(raw.includes("Call history"));
    assert.ok(raw.includes("Filters: Direction: incoming"));
    assert.ok(raw.includes("30 calls"));
    assert.ok(raw.includes("Generated: 2026-11-21 10:00:00 "));
    assert.ok(raw.includes("AI summary: Wants a quote"));
    assert.ok(!raw.includes(NOT_COMPLETE_SENTENCE));
    assert.ok(raw.includes("/Title (Call history)"));
    assert.ok(raw.includes("/Author (Talk-Lee)"));
});

const SIGNATURE = { ...LOGO, width: 400, height: 100, includesName: true };
const nameOperators = (raw: string) => (raw.match(/\(Talk-Lee\) Tj/g) ?? []).length;

test("with a signature image the header does not print the brand name again; the footer still does", async () => {
    const rows = buildExportRows(Array.from({ length: 30 }, (_, i) => makeCall(i)), {}, "UTC");
    // compress:false so page text is searchable; the name is drawn as "(Talk-Lee) Tj" text operators.
    const old = text(await buildCallHistoryPdf(rows, meta(), brand, LOGO, { compress: false }));
    const bytes = await buildCallHistoryPdf(rows, meta(), brand, SIGNATURE, { compress: false });
    const withSig = text(bytes);
    const pages = pageCount(bytes);
    assert.ok(pages > 1);
    assert.equal(nameOperators(old), pages * 2, "old header: name in header and footer on every page");
    assert.equal(nameOperators(withSig), pages, "signature header: only the footer name on every page");
    assert.ok(withSig.includes("Call history"));
});

test("the signature is placed at its aspect ratio (400x100 -> 4:1, 25.5 pt tall)", async () => {
    const raw = text(await buildCallHistoryPdf([], meta(), brand, SIGNATURE, { compress: false }));
    const match = /([\d.]+) 0 0 ([\d.]+) ([\d.]+) ([\d.]+) cm\s*\/I\d+ Do/.exec(raw);
    assert.ok(match, "image placement operator found");
    assert.ok(Math.abs(Number(match[1]) / Number(match[2]) - 4) < 0.01);
    assert.ok(Math.abs(Number(match[2]) - 25.5) < 0.01);
});

test("an incomplete list says so in plain words", async () => {
    const rows = buildExportRows([makeCall(1)], {}, "UTC");
    const bytes = await buildCallHistoryPdf(rows, meta({ complete: false, filters: [] }), brand, null, { compress: false });
    const raw = text(bytes);
    assert.ok(raw.includes("This file was made before every call had finished loading"));
    assert.ok(raw.includes("All calls"));
});

test("emoji, curly quotes and non-Latin letters still produce a file, with plain stand-ins", async () => {
    const note: CallHistoryWorkflowEntry = {
        leadType: "hot", notes: "Said “yes” – great 😀 it’s fine…",
        form: { contact: "Zoë Жук", interest: "", nextStep: "", completed: false }, updatedAt: "x",
    };
    const call = makeCall(1, { campaign_name: "Café 中文" });
    const rows = buildExportRows([call], { [call.id]: note }, "UTC");
    const bytes = await buildCallHistoryPdf(rows, meta(), brand, null, { compress: false });
    assert.equal(text(bytes).slice(0, 4), "%PDF");
    assert.equal(pdfSafeText(note.notes), "Said \"yes\" - great ? it's fine...");
    assert.equal(pdfSafeText("Zoë Жук"), "Zoë ???");
    assert.equal(pdfSafeText("š č"), "s c");
});

test("an empty list still produces a PDF", async () => {
    const bytes = await buildCallHistoryPdf([], meta(), brand, null, { compress: false });
    assert.equal(text(bytes).slice(0, 4), "%PDF");
    assert.equal(pageCount(bytes), 1);
});
