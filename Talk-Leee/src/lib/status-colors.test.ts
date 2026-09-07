import assert from "node:assert/strict";
import test from "node:test";

import {
    billingHoldReasonLabel,
    billingStatusLabel,
    callStatusLabel,
    toneFor,
    transferLegStatusLabel,
} from "@/lib/status-colors";

test("termination_pending is graded as in-flight, not as a failure", () => {
    // The backend writes this fence BEFORE the provider has proved the channel
    // gone and documents it as non-terminal — it releases no billing, quota or
    // concurrency (call_status.py:97, termination.py:262-270). Grading it red
    // would show the operator an outcome the server has not reached.
    assert.equal(toneFor("termination_pending"), "amber");
    assert.notEqual(toneFor("termination_pending"), "red");
    assert.notEqual(toneFor("termination_pending"), "muted");
});

test("callStatusLabel maps the declared status and leaves every other value raw", () => {
    assert.equal(callStatusLabel("termination_pending"), "Ending");
    assert.equal(callStatusLabel("TERMINATION_PENDING"), "Ending");
    assert.equal(callStatusLabel("  termination_pending  "), "Ending");
});

test("callStatusLabel returns an undeclared status verbatim rather than guessing", () => {
    // A status this map has never heard of must reach the operator exactly as
    // the server sent it. `status` has no published enum (OQ-CH-03), so the
    // fallback is the contract, not a stopgap.
    assert.equal(callStatusLabel("completed"), "completed");
    assert.equal(callStatusLabel("no_answer"), "no_answer");
    assert.equal(callStatusLabel("some_status_added_later"), "some_status_added_later");
    // A bare object lookup also answers for inherited members, which would
    // render a function where the server sent a word.
    assert.equal(callStatusLabel("constructor"), "constructor");
    assert.equal(callStatusLabel("toString"), "toString");
});

test("callStatusLabel renders nothing for a null or empty status", () => {
    assert.equal(callStatusLabel(undefined), "");
    assert.equal(callStatusLabel(null), "");
    assert.equal(callStatusLabel(""), "");
    assert.equal(callStatusLabel("   "), "");
});

test("transferLegStatusLabel maps every declared call_legs status", () => {
    // completed / failed            — inbound_transfer.py:1408
    // reconciliation_required       — inbound_transfer.py:1544-1556
    // initiated / ringing / answered — termination.py:17-24
    assert.equal(transferLegStatusLabel("completed"), "Completed");
    assert.equal(transferLegStatusLabel("failed"), "Failed");
    assert.equal(transferLegStatusLabel("reconciliation_required"), "Awaiting reconciliation");
    assert.equal(transferLegStatusLabel("initiated"), "Dialing");
    assert.equal(transferLegStatusLabel("ringing"), "Ringing");
    assert.equal(transferLegStatusLabel("answered"), "Answered");
});

test("transferLegStatusLabel returns an undeclared leg status verbatim", () => {
    // `call_legs.status` is an unconstrained varchar
    // (database/schema/baseline_2026-06-02.sql:521), so an unseen value must
    // reach the operator rather than be hidden or guessed at.
    assert.equal(transferLegStatusLabel("some_status_added_later"), "some_status_added_later");
    assert.equal(transferLegStatusLabel("constructor"), "constructor");
});

test("transferLegStatusLabel renders nothing for a null or empty leg status", () => {
    assert.equal(transferLegStatusLabel(undefined), "");
    assert.equal(transferLegStatusLabel(null), "");
    assert.equal(transferLegStatusLabel("  "), "");
});

test("a held transfer leg is graded as needing attention, not as a failure", () => {
    // Its billing is `held` pending adjudication, not settled —
    // inbound_transfer.py:1544-1556.
    assert.equal(toneFor("reconciliation_required"), "amber");
    assert.notEqual(toneFor("reconciliation_required"), "red");
});

test("every billing status the CHECK constraint declares gets a label", () => {
    // The vocabulary is closed by the database, so this map can be complete —
    // backend/Alembic/versions/0022_inbound_calling_foundation.py:706.
    assert.equal(billingStatusLabel("none"), "Not billed");
    assert.equal(billingStatusLabel("reserved"), "Reserved");
    assert.equal(billingStatusLabel("held"), "Held for review");
    assert.equal(billingStatusLabel("finalized"), "Settled");
    assert.equal(billingStatusLabel("released"), "Released");
    assert.equal(billingStatusLabel("reversed"), "Reversed");
});

test("a billing status this map has not seen is shown exactly as the server sent it", () => {
    // A value added to the CHECK constraint after this map was written must
    // reach the operator unchanged rather than being hidden or guessed at.
    assert.equal(billingStatusLabel("part_settled"), "part_settled");
    assert.equal(billingStatusLabel("  Adjudicated  "), "Adjudicated");
    // A plain object also answers for inherited members, so a status that
    // happens to name one must not render a function.
    assert.equal(billingStatusLabel("constructor"), "constructor");
    assert.equal(billingStatusLabel(null), "");
    assert.equal(billingStatusLabel(undefined), "");
});

test("every billing hold reason the CHECK constraint declares gets a label", () => {
    // backend/Alembic/versions/0032_inbound_billing_hold.py:83-87.
    assert.equal(
        billingHoldReasonLabel("settlement_switch_disabled"),
        "Settlement is switched off platform-wide",
    );
    assert.equal(
        billingHoldReasonLabel("usage_exceeded_reservation"),
        "Call ran longer than the minutes reserved for it",
    );
    assert.equal(
        billingHoldReasonLabel("provider_answer_ambiguous"),
        "Carrier records needed to confirm the call was answered",
    );
});

test("a hold reason this map has not seen is shown exactly as the server sent it", () => {
    assert.equal(billingHoldReasonLabel("carrier_dispute"), "carrier_dispute");
    assert.equal(billingHoldReasonLabel("constructor"), "constructor");
    assert.equal(billingHoldReasonLabel(null), "");
    assert.equal(billingHoldReasonLabel(undefined), "");
});
