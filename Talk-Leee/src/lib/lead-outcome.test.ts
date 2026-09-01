import assert from "node:assert/strict";
import test from "node:test";

import { leadInterestState } from "@/lib/lead-outcome";

test("positive lead verdicts produce the interested state", () => {
    for (const value of [
        "qualified | strong fit",
        "interested",
        "callback | Tuesday morning",
        "goal_achieved",
    ]) {
        assert.equal(leadInterestState(value), "interested", value);
    }
});

test("captured-detail-adjacent negative verdicts never produce an interested badge", () => {
    for (const value of ["no_interest", "not interested", "disqualified", "unsuccessful"]) {
        assert.equal(leadInterestState(value), "not_interested", value);
    }
});

test("missing and telephony-only outcomes remain unknown", () => {
    assert.equal(leadInterestState(null), "unknown");
    assert.equal(leadInterestState("answered"), "unknown");
});
