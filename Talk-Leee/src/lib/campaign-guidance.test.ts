import assert from "node:assert/strict";
import { test } from "node:test";
import {
    CAMPAIGN_GUIDANCE_CHAR_BUDGET,
    CAMPAIGN_GUIDANCE_MIN_CHARS,
    guidanceBudgetStatus,
} from "@/lib/campaign-guidance";

test("the default budget matches the backend's TELEPHONY_TENANT_PROMPT_MAX_CHARS default", () => {
    assert.equal(CAMPAIGN_GUIDANCE_CHAR_BUDGET, 12000);
});

test("guidance under budget is fine and reports what is left", () => {
    const text = "Call UK dental practices and book a demo of the booking system.";
    const s = guidanceBudgetStatus(text);
    assert.equal(s.chars, text.length);
    assert.equal(s.budget, 12000);
    assert.equal(s.overBudget, false);
    assert.equal(s.remaining, 12000 - text.length);
    assert.equal(s.valid, true);
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

test("guidance is compulsory: empty or whitespace is invalid with a clear message", () => {
    for (const text of ["", "   ", "\n\t"]) {
        const s = guidanceBudgetStatus(text);
        assert.equal(s.tooShort, true);
        assert.equal(s.valid, false);
        assert.match(s.message ?? "", /required/i);
    }
});

test("guidance shorter than the minimum is invalid and says how much is needed", () => {
    const s = guidanceBudgetStatus("Be nice.");
    assert.equal(s.tooShort, true);
    assert.equal(s.valid, false);
    assert.match(s.message ?? "", new RegExp(String(CAMPAIGN_GUIDANCE_MIN_CHARS)));
});

test("guidance at the minimum and under budget is valid", () => {
    const s = guidanceBudgetStatus("x".repeat(CAMPAIGN_GUIDANCE_MIN_CHARS));
    assert.equal(s.tooShort, false);
    assert.equal(s.overBudget, false);
    assert.equal(s.valid, true);
    assert.equal(s.message, null);
});

test("over budget is never valid", () => {
    assert.equal(guidanceBudgetStatus("x".repeat(12001)).valid, false);
});
