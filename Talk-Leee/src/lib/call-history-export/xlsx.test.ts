import assert from "node:assert/strict";
import test from "node:test";
import ExcelJS from "exceljs";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import { CALL_EXPORT_COLUMNS, buildExportRows, NOT_COMPLETE_SENTENCE, type CallExportMeta } from "./columns";
import type { Call } from "@/lib/dashboard-api";
import { buildCallHistoryWorkbook } from "./xlsx";

function makeCall(over: Partial<Call> = {}): Call {
    return {
        id: "11111111-aaaa-bbbb-cccc-222222222222", campaign_id: "camp-1", campaign_name: "Spring outreach", lead_id: "lead-1",
        phone_number: "+447429916656", status: "completed", outcome: "goal_achieved", duration_seconds: 125,
        created_at: "2026-11-21T23:30:00Z", summary: "Wants a quote.", recording_id: "rec-1", lead_outcome: "qualified | pricing",
        best_time_to_call: "Mornings", captured_phone: "+447429916656", captured_email: "ann@example.com",
        captured_phone_confirmed: true, captured_email_confirmed: true, has_feedback: true, direction: "outbound",
        from_number: "+442046132300", to_number: "+447429916656", ...over,
    };
}

const brand = { name: "Talk-Lee", logoSrc: "/favicon.svg" };
// 1x1 transparent PNG
const LOGO = {
    dataUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
    width: 1,
    height: 1,
};
const meta = (over: Partial<CallExportMeta> = {}): CallExportMeta => ({
    accountName: "Acme Ltd",
    timeZone: "Asia/Karachi",
    filters: ["Direction: incoming"],
    generatedAt: new Date("2026-11-21T10:00:00Z"),
    complete: true,
    ...over,
});

function rows() {
    const evil: CallHistoryWorkflowEntry = {
        leadType: "hot",
        notes: '=HYPERLINK("http://x","y")',
        form: { contact: "@SUM(A1)", interest: "-2+3", nextStep: "+1", completed: false },
        updatedAt: "x",
    };
    const calls = [
        makeCall({ id: "c1", captured_phone: "+447700900123", campaign_name: "=cmd|' /C calc'!A0" }),
        makeCall({ id: "c2", duration_seconds: 3700 }),
        makeCall({ id: "c3", outcome: "no_answer", direction: "inbound" }),
    ];
    return buildExportRows(calls, { c1: evil }, "Asia/Karachi");
}

async function read(m: CallExportMeta, logo: typeof LOGO | null = null) {
    const bytes = await buildCallHistoryWorkbook(rows(), m, brand, logo);
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load(bytes as unknown as ArrayBuffer);
    return { bytes, wb };
}

function allText(ws: ExcelJS.Worksheet): string[] {
    const out: string[] = [];
    ws.eachRow((row) => row.eachCell((cell) => out.push(String(cell.value ?? ""))));
    return out;
}

test("workbook has the two sheets, header cells, frozen pane, filter and properties", async () => {
    const { bytes, wb } = await read(meta(), LOGO);
    assert.equal(String.fromCharCode(bytes[0], bytes[1]), "PK");
    assert.deepEqual(wb.worksheets.map((w) => w.name), ["Call history", "Summary"]);
    assert.equal(wb.creator, "Talk-Lee");
    assert.equal(wb.created.toISOString(), "2026-11-21T10:00:00.000Z");
    const ws = wb.getWorksheet("Call history")!;
    // header block: account, generated, filters, count, then blank, then the table header
    const text = allText(ws);
    assert.ok(text.includes("Account: Acme Ltd"));
    assert.ok(text.includes("Generated: 2026-11-21 15:00:00 (Asia/Karachi)"));
    assert.ok(text.includes("Filters: Direction: incoming"));
    assert.ok(text.includes("Calls in this file: 3"));
    let headerRow = 0;
    ws.eachRow((row, n) => { if (row.getCell(1).value === "Date and time") headerRow = n; });
    assert.ok(headerRow > 0);
    CALL_EXPORT_COLUMNS.forEach((column, i) => assert.equal(ws.getRow(headerRow).getCell(i + 1).value, column.header));
    const view = ws.views[0] as { state?: string; xSplit?: number; ySplit?: number };
    assert.equal(view.state, "frozen");
    assert.equal(view.xSplit, 1);
    assert.equal(view.ySplit, headerRow);
    // exceljs hands the filter back as an "A7:AB7" range string after a load
    const lastLetter = ws.getRow(headerRow).getCell(CALL_EXPORT_COLUMNS.length).address.replace(/\d+$/, "");
    assert.equal(ws.autoFilter, `A${headerRow}:${lastLetter}${headerRow}`);
    assert.equal(ws.pageSetup.orientation, "landscape");
    assert.equal(ws.pageSetup.fitToWidth, 1);
    assert.equal(ws.pageSetup.printTitlesRow, `${headerRow}:${headerRow}`);
    assert.equal(ws.getImages().length, 1);
    // dates are text in the account zone, seconds is a real number
    const dateCol = CALL_EXPORT_COLUMNS.findIndex((c) => c.key === "date") + 1;
    const secCol = CALL_EXPORT_COLUMNS.findIndex((c) => c.key === "durationSeconds") + 1;
    assert.equal(ws.getRow(headerRow + 1).getCell(dateCol).value, "2026-11-22 04:30:00");
    assert.equal(ws.getRow(headerRow + 2).getCell(secCol).value, 3700);
    assert.equal(ws.getRow(headerRow + 2).getCell(secCol).type, ExcelJS.ValueType.Number);
    // summary sheet
    const sum = allText(wb.getWorksheet("Summary")!);
    for (const word of ["Totals", "By result", "By lead type", "By campaign", "Total talk time (h:mm:ss)"]) assert.ok(sum.includes(word), word);
    assert.ok(sum.includes("1:05:50")); // 125 + 3700 + 125 seconds of talk time
});

test("the not-complete line appears only when complete is false", async () => {
    const incomplete = allText((await read(meta({ complete: false }))).wb.getWorksheet("Call history")!);
    const complete = allText((await read(meta({ complete: true }))).wb.getWorksheet("Call history")!);
    assert.ok(incomplete.includes(NOT_COMPLETE_SENTENCE));
    assert.ok(!complete.includes(NOT_COMPLETE_SENTENCE));
});

test("no filters reads All calls", async () => {
    const text = allText((await read(meta({ filters: [] }))).wb.getWorksheet("Call history")!);
    assert.ok(text.includes("All calls"));
});

test("call data that looks like a formula or link stays a plain string cell", async () => {
    const { wb } = await read(meta());
    const ws = wb.getWorksheet("Call history")!;
    let headerRow = 0;
    ws.eachRow((row, n) => { if (row.getCell(1).value === "Date and time") headerRow = n; });
    const col = (key: string) => CALL_EXPORT_COLUMNS.findIndex((c) => c.key === key) + 1;
    const first = ws.getRow(headerRow + 1);
    const checks: Array<[string, string]> = [
        ["notes", '=HYPERLINK("http://x","y")'],
        ["formContact", "@SUM(A1)"],
        ["formInterest", "-2+3"],
        ["formNextStep", "+1"],
        ["campaign", "=cmd|' /C calc'!A0"],
    ];
    for (const [key, expected] of checks) {
        const cell = first.getCell(col(key));
        assert.equal(cell.type, ExcelJS.ValueType.String, key);
        assert.equal(cell.value, expected, key);
    }
    // the +44 number the caller gave: formatted text, string type
    const given = first.getCell(col("numberGiven"));
    assert.equal(given.type, ExcelJS.ValueType.String);
    assert.equal(typeof given.value, "string");
    assert.ok(String(given.value).startsWith("+44"));
    assert.equal(first.getCell(col("callId")).value, "c1");
});

test("the raw +447700900123 survives as a string when it is written unformatted", async () => {
    const wsBytes = await buildCallHistoryWorkbook(
        [{ date: "x", from: "+447700900123", notes: '=HYPERLINK("http://x","y")' }],
        meta(), brand, null,
    );
    const wb = new ExcelJS.Workbook();
    await wb.xlsx.load(wsBytes as unknown as ArrayBuffer);
    const ws = wb.getWorksheet("Call history")!;
    let headerRow = 0;
    ws.eachRow((row, n) => { if (row.getCell(1).value === "Date and time") headerRow = n; });
    const col = (key: string) => CALL_EXPORT_COLUMNS.findIndex((c) => c.key === key) + 1;
    const from = ws.getRow(headerRow + 1).getCell(col("from"));
    const note = ws.getRow(headerRow + 1).getCell(col("notes"));
    assert.equal(from.type, ExcelJS.ValueType.String);
    assert.equal(from.value, "+447700900123");
    assert.equal(note.type, ExcelJS.ValueType.String);
    assert.equal(note.value, '=HYPERLINK("http://x","y")');
});
