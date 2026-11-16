/**
 * FX6a Task 1: the pieces under the top-up card's "closed" reason.
 * - GET /billing/config is read as { topups_open, topups_unavailable_reason }.
 * - The 503 after a click says "Minute top-ups are not available yet" with no
 *   full stop; the config reason ends with one. Both are the same reason.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import { classifyBillingRefusal, sameBillingReason } from "@/lib/billing-refusal";
import { billingConfigSchema, topupKeys, topupsClosedReason } from "@/lib/topup-api";

// backend/app/domain/services/billing_mode.py:79 and backend/app/api/v1/endpoints/billing_topups.py:265.
const CONFIG_REASON = "Minute top-ups are not available yet.";
const REFUSAL_503 = "Minute top-ups are not available yet";

test("the config answer keeps topups_open and the server's reason", () => {
    const parsed = billingConfigSchema.parse({
        stripe_configured: true, mock_mode: false, billing_mode: "test", publishable_key: null,
        checkout_open: true, checkout_unavailable_reason: null, topups_open: false, topups_unavailable_reason: CONFIG_REASON,
    });
    assert.equal(parsed.topups_open, false);
    assert.equal(parsed.topups_unavailable_reason, CONFIG_REASON);
    assert.equal(billingConfigSchema.safeParse({ stripe_configured: true }).success, false, "an answer without topups_open is not a config answer");
    assert.deepEqual(topupKeys.config(), ["billing", "config"]);
});

test("the closed reason is the server's own text, and only when it says closed", () => {
    assert.equal(topupsClosedReason({ topups_open: false, topups_unavailable_reason: CONFIG_REASON }), CONFIG_REASON);
    assert.equal(topupsClosedReason({ topups_open: true, topups_unavailable_reason: null }), null);
    assert.equal(topupsClosedReason({ topups_open: true, topups_unavailable_reason: CONFIG_REASON }), null, "open wins over a leftover reason");
    assert.equal(topupsClosedReason({ topups_open: false, topups_unavailable_reason: null }), null, "no reason is made up");
    assert.equal(topupsClosedReason({ topups_open: false }), null);
    assert.equal(topupsClosedReason({ topups_open: false, topups_unavailable_reason: "  " }), null);
    assert.equal(topupsClosedReason(null), null, "an unread config closes nothing");
    assert.equal(topupsClosedReason(undefined), null);
});

test("a reason with and without its full stop is the same reason", () => {
    assert.equal(sameBillingReason(REFUSAL_503, CONFIG_REASON), true);
    assert.equal(sameBillingReason(CONFIG_REASON, CONFIG_REASON), true);
    assert.equal(sameBillingReason(` ${REFUSAL_503} `, CONFIG_REASON), true);
    assert.equal(sameBillingReason("Payments are temporarily unavailable.", CONFIG_REASON), false);
    assert.equal(sameBillingReason("Minute top-ups are not available", CONFIG_REASON), false);
});

test("the top-ups-off 503 is recognised in either spelling, and keeps the server's own text", () => {
    assert.deepEqual(classifyBillingRefusal(503, undefined, REFUSAL_503), { code: "topups_unavailable", message: REFUSAL_503, scope: "topup" });
    assert.deepEqual(classifyBillingRefusal(503, undefined, CONFIG_REASON), { code: "topups_unavailable", message: CONFIG_REASON, scope: "topup" });
    assert.equal(classifyBillingRefusal(503, undefined, "Something else is unavailable"), null);
    assert.equal(classifyBillingRefusal(503, undefined, undefined), null);
    assert.equal(classifyBillingRefusal(500, undefined, CONFIG_REASON), null, "only the 503 is this refusal");
});
