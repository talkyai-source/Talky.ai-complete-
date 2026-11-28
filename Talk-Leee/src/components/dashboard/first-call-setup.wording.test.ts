import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

// Source-text guard: the steps array in first-call-setup.tsx is not exported.
// Run from the frontend package folder (as npm test does).
const source = readFileSync(path.join(process.cwd(), "src/components/dashboard/first-call-setup.tsx"), "utf8");
const plan = /title: "Choose your plan",\s*href: "\/billing",\s*description: "([^"]+)"/.exec(source)?.[1] ?? "";

test("W10-T4: the plan step names Pay as you go beside a paid plan", () => {
    // backend/app/domain/services/payg.py:1-6 (prepaid minutes without a subscription),
    // minutes_quota.py:107-114 (a prepaid plan may use the minutes it bought).
    assert.match(plan, /Pay as you go/);
    assert.match(plan, /paid plan/);
    assert.doesNotMatch(plan, /free trial/i);
    assert.equal(plan, "Calls need a paid plan or Pay as you go minutes. Choose in Billing, then check your minutes.");
});

test("the plan step is no longer than the sentence it replaced", () => {
    // This card sits above the dashboard's first row. A longer sentence wraps to
    // one more line at phone width and moves that row, which the pixel
    // comparison in tests/dashboard-first-row.visual.spec.ts then rejects.
    const replaced = "Calls need a paid plan. Pick monthly or yearly in Billing, then check your included minutes.";
    assert.ok(plan.length <= replaced.length, `${plan.length} characters; the old sentence had ${replaced.length}`);
});
