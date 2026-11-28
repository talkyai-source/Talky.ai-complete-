import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

// Source-text guard: the steps array in first-call-setup.tsx is not exported.
// Run from the frontend package folder (as npm test does).
const source = readFileSync(path.join(process.cwd(), "src/components/dashboard/first-call-setup.tsx"), "utf8");
const plan = /title: "Choose your plan",\s*href: "\/billing",\s*description: "([^"]+)"/.exec(source)?.[1] ?? "";

test("W10-T4: the plan step names Pay as you go and still covers monthly and yearly plans", () => {
    // backend/app/domain/services/payg.py:1-6 (prepaid minutes without a subscription),
    // minutes_quota.py:107-114 (a prepaid plan may use the minutes it bought).
    assert.match(plan, /Pay as you go/);
    assert.match(plan, /monthly or yearly/);
    assert.doesNotMatch(plan, /free trial/i);
    assert.equal(plan, "Calls need a monthly or yearly plan, or Pay as you go minutes. Choose in Billing, then check your minutes.");
});
