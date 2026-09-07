import { test } from "node:test";
import assert from "node:assert/strict";
import { formSummaryLines, leadTypeAccent } from "@/components/calls/call-history-workflow-controls";

test("leadTypeAccent gives each lead type its own ring colour matching the select", () => {
    assert.match(leadTypeAccent("warm").ring, /orange/);
    assert.match(leadTypeAccent("cold").ring, /red/);
    assert.match(leadTypeAccent("hot").ring, /emerald/);
    assert.match(leadTypeAccent("follow_up").ring, /sky/);
    assert.equal(leadTypeAccent("warm").label, "Warm");
});

test("formSummaryLines lists only the filled post-call fields, in reading order", () => {
    assert.deepEqual(formSummaryLines({ contact: "", interest: "", nextStep: "", completed: false }), []);
    assert.deepEqual(
        formSummaryLines({ contact: " Sam Owner ", interest: "", nextStep: "Call Tuesday", completed: true }),
        [["Contact", "Sam Owner"], ["Next step", "Call Tuesday"]]
    );
});
