import assert from "node:assert/strict";
import { test } from "node:test";
import { classifyCall, inferCallLeadType } from "@/lib/call-history-workflow";

// The server writes status "completed" for every finished call; the outcome
// carries the truth (backend call_outcomes.py FAILED_OUTCOMES).
for (const outcome of ["voicemail", "unreachable", "cancelled", "network_failure"]) {
    test(`a completed call with outcome ${outcome} is failed, not answered, and cold`, () => {
        assert.deepEqual(classifyCall({ status: "completed", outcome }), { answered: false, failed: true });
        assert.equal(inferCallLeadType({ status: "completed", outcome, lead_outcome: null, captured_phone: null, captured_email: null }), "cold");
    });
}

for (const outcome of ["answered", "goal_achieved"]) {
    test(`a completed call with outcome ${outcome} is answered`, () => {
        assert.deepEqual(classifyCall({ status: "completed", outcome }), { answered: true, failed: false });
    });
}

test("without an outcome the status decides", () => {
    assert.deepEqual(classifyCall({ status: "completed" }), { answered: true, failed: false });
    assert.deepEqual(classifyCall({ status: "no_answer" }), { answered: false, failed: true });
});

test("a voicemail call that captured a contact stays hot", () => {
    assert.equal(inferCallLeadType({ status: "completed", outcome: "voicemail", lead_outcome: null, captured_phone: "+447400123456", captured_email: null }), "hot");
});
