import assert from "node:assert/strict";
import { test } from "node:test";

import { isMinutesExhausted, type MinutesStatus } from "@/lib/dashboard-api";

function status(overrides: Partial<MinutesStatus>): MinutesStatus {
    return {
        allocated: 500,
        used_minutes: 0,
        remaining_minutes: 500,
        unlimited: false,
        exhausted: false,
        ...overrides,
    };
}

test("a metered tenant with no minutes left is exhausted", () => {
    assert.equal(
        isMinutesExhausted(status({ allocated: 500, used_minutes: 500, remaining_minutes: 0, exhausted: true })),
        true,
    );
});

test("a metered tenant with minutes remaining is not exhausted", () => {
    assert.equal(
        isMinutesExhausted(status({ allocated: 500, used_minutes: 100, remaining_minutes: 400, exhausted: false })),
        false,
    );
});

test("an unlimited plan is never exhausted, even if the server marks it so", () => {
    // `unlimited` (allocated 0) is the field to branch on — the same rule
    // documented at dashboard-api.ts:26 for the outbound Start button.
    assert.equal(
        isMinutesExhausted(status({ unlimited: true, allocated: 0, remaining_minutes: 0, exhausted: true })),
        false,
    );
});

test("a status that has not loaded yet is treated as not exhausted, never guessed", () => {
    assert.equal(isMinutesExhausted(null), false);
    assert.equal(isMinutesExhausted(undefined), false);
});
