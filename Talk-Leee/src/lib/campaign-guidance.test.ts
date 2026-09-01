import assert from "node:assert/strict";
import { test } from "node:test";
import {
    CAMPAIGN_GUIDANCE_CHAR_BUDGET,
    guidanceBudgetStatus,
} from "@/lib/campaign-guidance";

test("the default budget matches the backend's TELEPHONY_TENANT_PROMPT_MAX_CHARS default", () => {
    assert.equal(CAMPAIGN_GUIDANCE_CHAR_BUDGET, 12000);
});

test("guidance under budget is fine and reports what is left", () => {
    const s = guidanceBudgetStatus("hello world");
    assert.equal(s.chars, 11);
    assert.equal(s.budget, 12000);
    assert.equal(s.overBudget, false);
    assert.equal(s.remaining, 11989);
    assert.equal(s.message, null);
});

test("guidance over budget is flagged with the numbers, and never trimmed", () => {
    const text = "x".repeat(12001);
    const s = guidanceBudgetStatus(text);
    assert.equal(s.overBudget, true);
    assert.equal(s.remaining, -1);
    assert.match(s.message ?? "", /12,001/);
    assert.match(s.message ?? "", /12,000/);
    assert.match(s.message ?? "", /Company knowledge/);
});

test("a server-supplied budget overrides the default", () => {
    const s = guidanceBudgetStatus("x".repeat(700), 600);
    assert.equal(s.budget, 600);
    assert.equal(s.overBudget, true);
});
