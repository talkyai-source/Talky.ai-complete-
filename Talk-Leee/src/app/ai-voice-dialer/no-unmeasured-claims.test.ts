import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// QA F1-04 (QA-UI-111): the AI voice dialer page promised "10x more dialing
// capacity", "Pre-qualified leads, every time" and "Zero missed follow-ups".
// Nothing measures them (no capacity test, follow-ups depend on connectors
// that can fail). A guard over the literal copy, since the defect is the copy.
const page = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "page.tsx"), "utf8");

for (const claim of [/\b\d+x\s+more\b/i, /zero\s+missed/i, /every\s+time/i]) {
    test(`the dialer page makes no unmeasured claim matching ${claim}`, () => {
        assert.equal(page.match(claim), null);
    });
}
