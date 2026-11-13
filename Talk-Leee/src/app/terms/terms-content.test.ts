import { test } from "node:test";
import assert from "node:assert/strict";
import { termsBlocks } from "./terms-content";

// QA F1-01 / F13-13 (owner decision, 11 Nov 2026): the Terms must match what
// the product does. Talklee is paid-only, so there is no trial to describe;
// there is no monitored SLA, so no uptime percentage is promised; and minutes
// are counted the way the pricing page says. A guard over the literal copy,
// since the defect is the copy.
const text = termsBlocks
    .flatMap((block) => [
        "title" in block ? block.title : "",
        "text" in block ? block.text : "",
        ...("items" in block ? block.items : []),
        ...("rows" in block ? block.rows.flat() : []),
    ])
    .join("\n");

test("the Terms describe no trial", () => {
    assert.equal(/\btrial/i.test(text), false);
});

test("the Terms promise no uptime percentage", () => {
    assert.equal(/\d+(\.\d+)?\s*%\s*(monthly\s+)?uptime/i.test(text), false);
    assert.equal(/uptime/i.test(text), false);
});

test("the Terms state how call minutes are counted, as the pricing page does", () => {
    assert.match(text, /recorded duration of your calls/);
    assert.match(text, /whole minutes, rounded down/);
    assert.match(text, /renews from the date you subscribe/);
});
