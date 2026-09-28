import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
    capturedContactParts,
    classifyCall,
    formatPhoneForDisplay,
    inferCallLeadType,
    isCallHistoryFormComplete,
    isActiveCallStatus,
    readCallHistoryWorkflow,
    writeCallHistoryWorkflow,
    type CallHistoryWorkflowMap,
} from "@/lib/call-history-workflow";
import { ensureDom } from "@/test-utils/dom";

ensureDom();

afterEach(() => {
    window.localStorage.clear();
});

test("AI outcomes seed useful lead types without replacing a saved choice", () => {
    assert.equal(inferCallLeadType({ status: "completed", outcome: "goal_achieved", lead_outcome: "qualified | booked" }), "hot");
    assert.equal(inferCallLeadType({ status: "completed", outcome: "answered", lead_outcome: "callback | Friday" }), "follow_up");
    assert.equal(inferCallLeadType({ status: "failed", outcome: "failed", lead_outcome: null }), "cold");
    assert.equal(inferCallLeadType({ status: "failed", outcome: "answered", lead_outcome: null }), "cold");
    assert.equal(inferCallLeadType({ status: "completed", outcome: "answered", lead_outcome: null }), "warm");
});

test("a caller who left a phone or email is a hot lead whatever the verdict says", () => {
    // Live call b97ce4c5 (2026-09-23): the caller gave +923085397539, the
    // summary verdict was "callback", and the call never showed as hot.
    assert.equal(
        inferCallLeadType({ status: "ended", outcome: "answered", lead_outcome: "callback | x", captured_phone: "+923085397539" }),
        "hot",
    );
    assert.equal(
        inferCallLeadType({ status: "ended", outcome: "answered", lead_outcome: null, captured_email: "guide@gmail.com" }),
        "hot",
    );
    assert.equal(
        inferCallLeadType({ status: "ended", outcome: "answered", lead_outcome: null, captured_phone: "  ", captured_email: null }),
        "warm",
    );
});

test("review polling treats answered calls as live until a terminal state arrives", () => {
    assert.equal(isActiveCallStatus("answered"), true);
    assert.equal(isActiveCallStatus("in_call"), true);
    assert.equal(isActiveCallStatus("completed"), false);
    assert.equal(isActiveCallStatus("failed"), false);
});

test("workflow notes, lead type, and form state round-trip within an identity scope", () => {
    const value: CallHistoryWorkflowMap = {
        "call-1": {
            leadType: "follow_up",
            notes: "Send the pricing deck tomorrow.",
            form: {
                contact: "Ava",
                interest: "Annual plan",
                nextStep: "Email pricing",
                completed: true,
            },
            updatedAt: "2026-09-03T10:00:00.000Z",
        },
    };

    writeCallHistoryWorkflow("tenant-a", value);

    assert.deepEqual(readCallHistoryWorkflow("tenant-a"), value);
    assert.deepEqual(readCallHistoryWorkflow("tenant-b"), {});
});

test("malformed saved workflow is ignored safely", () => {
    window.localStorage.setItem("talklee.call-history.workflow.v1:tenant-a", "{not-json");
    assert.deepEqual(readCallHistoryWorkflow("tenant-a"), {});
});

test("post-call form is complete only when all three key fields have content", () => {
    assert.equal(isCallHistoryFormComplete({ contact: "Ava", interest: "Pricing", nextStep: "Call Friday", completed: false }), true);
    assert.equal(isCallHistoryFormComplete({ contact: "Ava", interest: " ", nextStep: "Call Friday", completed: true }), false);
});

test("a contact the caller said but never confirmed is listed and labelled", () => {
    // 2026-09-28: stated-but-unconfirmed contacts are now stored and shown.
    assert.deepEqual(
        capturedContactParts({ captured_phone: "+923085397539", captured_phone_confirmed: false, captured_email: "a@b.co", captured_email_confirmed: true }),
        [{ kind: "phone", value: "+92 308 5397539", confirmed: false }, { kind: "email", value: "a@b.co", confirmed: true }],
    );
    // An older API without the flag keeps today's (confirmed) rendering.
    assert.deepEqual(capturedContactParts({ captured_phone: "+923085397539" }), [{ kind: "phone", value: "+92 308 5397539", confirmed: true }]);
    assert.deepEqual(capturedContactParts({ captured_phone: "  ", captured_email: null }), []);
});

test("a finished answered call counts as answered (status ended, outcome answered)", () => {
    // 2026-09-28 Dojo-PC b847f447: status "ended", outcome "answered" read "0 answered".
    assert.deepEqual(classifyCall({ status: "ended", outcome: "answered" }), { answered: true, failed: false });
    assert.deepEqual(classifyCall({ status: "ended", outcome: "customer_hung_up" }), { answered: true, failed: false });
    assert.deepEqual(classifyCall({ status: "ended", outcome: "no_answer" }), { answered: false, failed: true });
    assert.deepEqual(classifyCall({ status: "ended", outcome: "voicemail" }), { answered: false, failed: false });
    assert.deepEqual(classifyCall({ status: "completed" }), { answered: true, failed: false });
});

test("contact numbers are shown in full, grouped the way people read them", () => {
    assert.equal(formatPhoneForDisplay("+923120750496"), "+92 312 0750496");
    assert.equal(formatPhoneForDisplay("+447429916656"), "+44 7429 916656");
    assert.equal(formatPhoneForDisplay("+16473476870"), "+1 647 347 6870");
    // Unknown layout: grouped, no digit dropped.
    assert.equal(formatPhoneForDisplay("+4791234567").replace(/ /g, ""), "+4791234567");
    // Extensions and non-E.164 values pass through untouched.
    assert.equal(formatPhoneForDisplay("940007"), "940007");
    assert.equal(formatPhoneForDisplay(null), "");
});
