import assert from "node:assert/strict";
import { test } from "node:test";

import { callerGaveProblem, correctCallerGave } from "./call-contact-correction";

test("a full email and a real phone number have no problem; an empty value is allowed", () => {
    assert.equal(callerGaveProblem("email", "sam@example.com"), null);
    assert.equal(callerGaveProblem("phone", "+44 20 7946 0958"), null);
    assert.equal(callerGaveProblem("phone", "(020) 7946-0958"), null);
    assert.equal(callerGaveProblem("email", "   "), null);
    assert.equal(callerGaveProblem("phone", ""), null);
});

test("a broken email or number is refused with a plain sentence", () => {
    assert.equal(callerGaveProblem("email", "sam@example"), "Enter a full email address, such as name@example.com.");
    assert.equal(callerGaveProblem("email", "sam example.com"), "Enter a full email address, such as name@example.com.");
    assert.equal(callerGaveProblem("phone", "12345"), "Enter a phone number with 7 to 15 digits.");
    assert.equal(callerGaveProblem("phone", "call me later"), "Enter a phone number with 7 to 15 digits.");
    assert.equal(callerGaveProblem("phone", "1234567890123456"), "Enter a phone number with 7 to 15 digits.");
});

test("a correction is sent trimmed, with the detail's own key and type", async () => {
    const sent: unknown[][] = [];
    const correct = async (...args: unknown[]) => { sent.push(args); };
    await correctCallerGave("call-1", "email", "  sam@example.com ", correct);
    await correctCallerGave("call-1", "phone", "+44 20 7946 0958", correct);
    assert.deepEqual(sent, [
        ["call-1", "email", "sam@example.com", "email"],
        ["call-1", "phone", "+44 20 7946 0958", "phone"],
    ]);
});

test("an empty value removes the detail: null is sent", async () => {
    const sent: unknown[][] = [];
    await correctCallerGave("call-1", "phone", "   ", async (...args: unknown[]) => { sent.push(args); });
    assert.deepEqual(sent, [["call-1", "phone", null, "phone"]]);
});

test("a value with a problem is never sent", async () => {
    let calls = 0;
    await assert.rejects(
        () => correctCallerGave("call-1", "email", "nope", async () => { calls += 1; }),
        /Enter a full email address/,
    );
    assert.equal(calls, 0);
});
