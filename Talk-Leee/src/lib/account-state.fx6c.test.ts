/**
 * FX6c Task 1 (plan 3.6). The profile answer and the signed-in user keep
 * account_state and account_state_reason; a missing value means active and
 * every answer from before these fields existed still parses.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { SUBSCRIPTION_SUSPENDED_TEXT, isSubscriptionSuspended } from "./account-state";
import { MeResponseSchema } from "./api";

const BASE = { id: "u1", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" };

test("the profile answer keeps account_state and account_state_reason", () => {
    const me = MeResponseSchema.parse({ ...BASE, account_state: "suspended_subscription", account_state_reason: "subscription_suspended" });
    assert.equal(me.account_state, "suspended_subscription");
    assert.equal(me.account_state_reason, "subscription_suspended");
    assert.equal(isSubscriptionSuspended(me), true);
});

test("an answer without the fields, or with nulls, still parses and means active", () => {
    const old = MeResponseSchema.parse(BASE);
    assert.equal(old.account_state, undefined);
    assert.equal(old.account_state_reason, undefined);
    assert.equal(isSubscriptionSuspended(old), false);
    const nulls = MeResponseSchema.parse({ ...BASE, account_state: "active", account_state_reason: null });
    assert.equal(nulls.account_state, "active");
    assert.equal(nulls.account_state_reason, undefined);
    assert.equal(isSubscriptionSuspended(nulls), false);
});

test("only suspended_subscription counts: a hold, active, no user and a value the app does not know do not", () => {
    assert.equal(isSubscriptionSuspended({ account_state: "suspended_hold" }), false);
    assert.equal(isSubscriptionSuspended({ account_state: "active" }), false);
    assert.equal(isSubscriptionSuspended({ account_state: "something_new" }), false);
    assert.equal(isSubscriptionSuspended(null), false);
    assert.equal(isSubscriptionSuspended(undefined), false);
    // A state the app does not know never breaks the profile read.
    assert.equal(MeResponseSchema.parse({ ...BASE, account_state: "something_new" }).account_state, "something_new");
});

test("the sentence is the server's own campaign Start refusal, word for word", () => {
    // backend/app/api/v1/endpoints/campaigns.py:178-181
    assert.equal(SUBSCRIPTION_SUSPENDED_TEXT, "This account is suspended, so campaigns cannot start. Billing and Support stay available.");
});
