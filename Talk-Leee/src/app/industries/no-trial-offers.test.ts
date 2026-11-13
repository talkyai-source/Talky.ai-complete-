import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// QA F1-01 (QA-UI-110): every industry page and the footer promised a free or
// 14-day trial ("Start Free", "Try It Free", "No credit card needed") that
// does not exist: Talklee is paid-only (monthly plans or pay-as-you-go
// minutes). A guard over the literal copy, since the defect is the copy.
const here = path.dirname(fileURLToPath(import.meta.url));
const sources = [
    ...readdirSync(here, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => path.join(here, entry.name, "page.tsx")),
    path.join(here, "..", "..", "components", "home", "footer.tsx"),
];
const TRIAL_PROMISE = /free\s*(\/|trial|account)|\d+[-\s]day\s+trial|start\s+(your\s+)?free|try\s+(it|ai)\s+free|no\s+credit\s+card/i;

for (const file of sources) {
    test(`${path.relative(path.join(here, "..", ".."), file)} promises no trial`, () => {
        const text = readFileSync(file, "utf8");
        const match = text.match(TRIAL_PROMISE);
        assert.equal(match, null, `trial promise: ${match?.[0]}`);
    });
}
