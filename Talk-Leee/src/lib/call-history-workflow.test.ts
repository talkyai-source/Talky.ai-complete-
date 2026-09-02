import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import {
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
