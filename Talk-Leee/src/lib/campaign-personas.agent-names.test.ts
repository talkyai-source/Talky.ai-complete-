import assert from "node:assert/strict";
import { test } from "node:test";

import { agentNamesOverflow, parseAgentNames } from "@/lib/campaign-personas";

// Forms audit B-F27: repeats (ignoring case) are removed, and a fourth name is
// reported as unused instead of being dropped silently.
test("repeated names are removed, ignoring case, keeping the first spelling", () => {
    assert.deepEqual(parseAgentNames("Alex, alex, Sam"), ["Alex", "Sam"]);
    assert.deepEqual(parseAgentNames("Alex\nALEX"), ["Alex"]);
});

test("at most three names are used and the overflow is reported", () => {
    assert.deepEqual(parseAgentNames("A, B, C, D"), ["A", "B", "C"]);
    assert.equal(agentNamesOverflow("A, B, C, D"), true);
    assert.equal(agentNamesOverflow("A, B, C"), false);
    assert.equal(agentNamesOverflow("A, a, B, b, C"), false);
});
