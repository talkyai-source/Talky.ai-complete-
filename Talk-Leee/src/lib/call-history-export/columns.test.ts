import assert from "node:assert/strict";
import test from "node:test";
import type { Call } from "@/lib/dashboard-api";
import type { CallHistoryWorkflowEntry } from "@/lib/call-history-workflow";
import { CALL_EXPORT_COLUMNS, buildExportRows, exportFileName, summarizeExport } from "./columns";

export function makeCall(over: Partial<Call> = {}): Call {
    return {
        id: "11111111-aaaa-bbbb-cccc-222222222222",
        campaign_id: "camp-1",
        campaign_name: "Spring outreach",
        lead_id: "lead-1",
        phone_number: "+447429916656",
        status: "completed",
        outcome: "goal_achieved",
        duration_seconds: 125,
        created_at: "2026-11-21T23:30:00Z",
        summary: "Wants a quote next week.",
        recording_id: "rec-1",
        lead_outcome: "qualified | asked for pricing",
        best_time_to_call: "Mornings",
        captured_phone: "+447429916656",
        captured_email: "ann@example.com",
        captured_phone_confirmed: true,
        captured_email_confirmed: true,
        has_feedback: true,
        direction: "outbound",
        from_number: "+442046132300",
        to_number: "+447429916656",
        ...over,
    };
}

const entry = (over: Partial<CallHistoryWorkflowEntry> = {}): CallHistoryWorkflowEntry => ({
    leadType: "warm",
    notes: "Call back Tuesday",
    form: { contact: "Ann", interest: "Premium", nextStep: "Send quote", completed: true },
    updatedAt: "2026-11-22T00:00:00Z",
    ...over,
});

test("column list is in the contract order and keys are unique", () => {
    const keys = CALL_EXPORT_COLUMNS.map((c) => c.key);
    assert.equal(new Set(keys).size, keys.length);
    assert.deepEqual(keys.slice(0, 9), ["date", "direction", "campaign", "from", "to", "result", "answered", "duration", "durationSeconds"]);
    assert.equal(keys[keys.length - 1], "callId");
    assert.equal(CALL_EXPORT_COLUMNS.find((c) => c.key === "durationSeconds")?.kind, "number");
});

test("an outbound answered call with everything fills every column", () => {
    const call = makeCall();
    const [row] = buildExportRows([call], { [call.id]: entry({ leadType: "hot" }) }, "UTC");
    assert.equal(row.date, "2026-11-21 23:30:00");
    assert.equal(row.direction, "Outbound");
    assert.equal(row.campaign, "Spring outreach");
    assert.equal(row.from, "+44 2046 132300");
    assert.equal(row.to, "+44 7429 916656");
    assert.equal(row.result, "Qualified");
    assert.equal(row.answered, "Yes");
    assert.equal(row.duration, "0:02:05");
    assert.equal(row.durationSeconds, 125);
    assert.equal(row.leadType, "Hot");
    assert.equal(row.aiVerdict, "Qualified - asked for pricing");
    assert.equal(row.emailGiven, "ann@example.com");
    assert.equal(row.emailConfirmed, "Yes");
    assert.equal(row.numberGiven, "+44 7429 916656");
    assert.equal(row.numberConfirmed, "Yes");
    assert.equal(row.bestTime, "Mornings");
    assert.equal(row.summary, "Wants a quote next week.");
    assert.equal(row.notes, "Call back Tuesday");
    assert.equal(row.formContact, "Ann");
    assert.equal(row.formInterest, "Premium");
    assert.equal(row.formNextStep, "Send quote");
    assert.equal(row.recording, "Yes");
    assert.equal(row.voiceNote, "Yes");
    assert.equal(row.callId, call.id);
});

test("an incoming call with nothing given says Not provided twice and leaves the rest empty", () => {
    const call = makeCall({
        direction: "inbound", outcome: "no_answer", captured_email: null, captured_phone: null,
        captured_email_confirmed: null, captured_phone_confirmed: null, recording_id: null, has_feedback: false,
        summary: undefined, lead_outcome: null, best_time_to_call: null, duration_seconds: undefined,
        from_number: "+15551234567", to_number: "+442046132300",
    });
    const [row] = buildExportRows([call], {}, "UTC");
    assert.equal(row.direction, "Incoming");
    assert.equal(row.emailGiven, "Not provided");
    assert.equal(row.numberGiven, "Not provided");
    assert.equal(row.emailConfirmed, "");
    assert.equal(row.numberConfirmed, "");
    assert.equal(row.result, "No Answer");
    assert.equal(row.answered, "No");
    assert.equal(row.failed, "Yes");
    assert.equal(row.duration, "");
    assert.equal(row.durationSeconds, null);
    assert.equal(row.recording, "No");
    assert.equal(row.voiceNote, "No");
    assert.equal(row.summary, "");
    assert.equal(row.leadType, "Cold");
});

test("an unconfirmed email is marked No", () => {
    const [row] = buildExportRows([makeCall({ captured_email_confirmed: false })], {}, "UTC");
    assert.equal(row.emailConfirmed, "No");
    assert.equal(row.numberConfirmed, "Yes");
});

test("a saved lead type overrides the inferred one", () => {
    const call = makeCall(); // inferred: hot (caller gave contact)
    const [inferred] = buildExportRows([call], {}, "UTC");
    const [saved] = buildExportRows([call], { [call.id]: entry({ leadType: "follow_up" }) }, "UTC");
    assert.equal(inferred.leadType, "Hot");
    assert.equal(saved.leadType, "Follow-up");
});

test("dates use the account time zone whatever the process zone is", () => {
    const call = makeCall({ created_at: "2026-11-21T23:30:00Z" });
    assert.equal(buildExportRows([call], {}, "Asia/Karachi")[0].date, "2026-11-22 04:30:00");
    assert.equal(buildExportRows([call], {}, "America/New_York")[0].date, "2026-11-21 18:30:00");
    assert.equal(buildExportRows([call], {}, "Not/AZone")[0].date, "2026-11-21 23:30:00");
    assert.equal(buildExportRows([makeCall({ created_at: "garbage" })], {}, "UTC")[0].date, "");
});

test("summarizeExport totals and groups", () => {
    const calls = [
        makeCall({ id: "a", duration_seconds: 60 }),
        makeCall({ id: "b", duration_seconds: 30, outcome: "no_answer", campaign_name: "Other" }),
        makeCall({ id: "c", duration_seconds: 10, outcome: "voicemail", campaign_name: "Other" }),
        makeCall({ id: "d", duration_seconds: undefined, outcome: "goal_achieved", campaign_name: undefined }),
    ];
    const s = summarizeExport(buildExportRows(calls, {}, "UTC"));
    assert.equal(s.total, 4);
    assert.equal(s.answered, 2);
    assert.equal(s.failed, 2);
    assert.equal(s.talkSeconds, 100);
    assert.deepEqual(s.byResult, [["Qualified", 2], ["No Answer", 1], ["Voicemail", 1]]);
    assert.deepEqual(s.byLeadType, [["Hot", 4]]); // every fixture call carries a captured contact
    assert.deepEqual(s.byCampaign, [["Other", 2], ["(no campaign)", 1], ["Spring outreach", 1]]);
});

test("exportFileName", () => {
    const now = new Date("2026-11-21T10:00:00Z");
    assert.equal(exportFileName("xlsx", now), "talk-lee-call-history-2026-11-21.xlsx");
    assert.equal(exportFileName("pdf", now, "ab12cd34-ffff"), "talk-lee-call-ab12cd34-2026-11-21.pdf");
});
