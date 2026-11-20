import assert from "node:assert/strict";
import test from "node:test";
import type { Call } from "@/lib/dashboard-api";
import { buildExportRows, type CallExportMeta } from "./columns";
import { buildSingleCallPdf, NO_TRANSCRIPT_SENTENCE, type SingleCallExport } from "./single-call-pdf";

const brand = { name: "Talk-Lee", logoSrc: "/favicon.svg" };
const meta: CallExportMeta = {
    accountName: "Acme Ltd", timeZone: "UTC", filters: [], generatedAt: new Date("2026-11-21T10:00:00Z"), complete: true,
};
const call: Call = {
    id: "abcdef12-0000-0000-0000-000000000000", campaign_id: "c", campaign_name: "Spring", lead_id: "l", phone_number: "+447429916656",
    status: "completed", outcome: "goal_achieved", duration_seconds: 90, created_at: "2026-11-21T09:00:00Z",
    summary: "The caller wants a quote.", captured_email: "ann@example.com", captured_email_confirmed: false, direction: "inbound",
};
const text = (bytes: Uint8Array) => Buffer.from(bytes).toString("latin1");
const pageCount = (bytes: Uint8Array) => (text(bytes).match(/\/Type\s*\/Page(?!s)/g) ?? []).length;

function detail(transcript: SingleCallExport["transcript"]): SingleCallExport {
    return {
        row: buildExportRows([call], {}, "UTC")[0],
        capturedFields: [{ label: "Email", value: "ann@example.com", source: "Said on the call", confirmed: false }],
        transcript,
    };
}

test("a 300-line transcript flows over several pages and keeps the sections", async () => {
    const transcript = Array.from({ length: 300 }, (_, i) => ({
        speaker: i % 2 ? "Caller" : "Agent", text: `Line number ${i} of the conversation.`, at: `0:${String(i % 60).padStart(2, "0")}`,
    }));
    const bytes = await buildSingleCallPdf(detail(transcript), meta, brand, null, { compress: false });
    const raw = text(bytes);
    assert.equal(raw.slice(0, 4), "%PDF");
    const pages = pageCount(bytes);
    assert.ok(pages > 1, `pages=${pages}`);
    assert.ok(raw.includes(`Page ${pages} of ${pages}`));
    for (const section of ["Call details", "Details captured on this call", "AI summary", "Notes and follow-up form", "Transcript"]) {
        assert.ok(raw.includes(section), section);
    }
    assert.ok(raw.includes("Line number 299 of the conversation."));
    assert.ok(raw.includes("Said on the call"));
    assert.ok(!raw.includes(NO_TRANSCRIPT_SENTENCE));
});

test("an empty transcript says there is none", async () => {
    const bytes = await buildSingleCallPdf(detail([]), meta, brand, null, { compress: false });
    assert.ok(text(bytes).includes("No transcript for this call."));
});
