/**
 * The four rules the top-up card makes decisions on.
 *
 * Each is here rather than inline in the render tree because each has a wrong
 * answer that costs something real: telling a customer their minutes arrived
 * when they haven't, selling a cap to someone who had none, or quoting a price
 * that isn't what the card will be charged.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import {
    ORDER_STATUS_LABEL,
    ORDER_STATUS_TONE,
    canTopUp,
    formatMoney,
    isLowBalance,
    type TopupBalance,
} from "@/lib/topup-api";

function balance(over: Partial<TopupBalance> = {}): TopupBalance {
    return {
        allocated: 1000,
        used_minutes: 100,
        remaining_minutes: 900,
        unlimited: false,
        exhausted: false,
        purchased_minutes: 0,
        topup_minutes_left: 0,
        ...over,
    };
}

// ── who gets offered a top-up ───────────────────────────────────────────────

test("an unlimited plan is never offered a top-up", () => {
    // Adding 250 minutes to an uncapped account replaces "no limit" with
    // "250 minutes". The backend refuses it, so offering it would take money
    // and change nothing.
    assert.equal(canTopUp(balance({ unlimited: true, allocated: 0 })), false);
});

test("a metered plan is offered a top-up", () => {
    assert.equal(canTopUp(balance()), true);
});

test("nothing is offered before the balance is known", () => {
    // A Buy button that appears and then vanishes once the balance loads is
    // worse than a beat of nothing.
    assert.equal(canTopUp(null), false);
});

// ── the low-balance warning ─────────────────────────────────────────────────

test("under 15% remaining reads as low", () => {
    assert.equal(
        isLowBalance(balance({ allocated: 1000, remaining_minutes: 149 })),
        true,
    );
});

test("exactly 15% remaining is not low yet", () => {
    assert.equal(
        isLowBalance(balance({ allocated: 1000, remaining_minutes: 150 })),
        false,
    );
});

test("an unlimited plan is never low", () => {
    assert.equal(
        isLowBalance(balance({ unlimited: true, allocated: 0, remaining_minutes: 0 })),
        false,
    );
});

test("a zero allocation does not divide by zero", () => {
    assert.equal(isLowBalance(balance({ allocated: 0, remaining_minutes: 0 })), false);
});

// ── money ───────────────────────────────────────────────────────────────────

test("prices render from minor units, not major", () => {
    // The API returns 2500 for £25. Rendering it as £2,500 is the kind of
    // mistake a customer notices before we do.
    assert.equal(formatMoney(2500, "GBP", 2), "£25.00");
});

test("a sub-penny per-minute rate keeps its precision", () => {
    assert.equal(formatMoney(10, "GBP", 2), "£0.10");
});

test("the currency comes from the package, not a hardcoded default", () => {
    assert.equal(formatMoney(2500, "USD", 2), "$25.00");
});

test("a missing currency is unavailable rather than an assumed GBP amount", () => {
    assert.equal(formatMoney(2500, "", 2), "Unavailable");
});

// ── every status the backend can return is renderable ───────────────────────

test("every order status the backend defines has a label and a tone", () => {
    // The backend CHECK constraint allows exactly these six. A status with no
    // entry renders as a raw enum value in front of a paying customer.
    const backendStates = [
        "pending",
        "paid",
        "failed",
        "cancelled",
        "refunded",
        "disputed",
    ] as const;
    for (const s of backendStates) {
        assert.ok(ORDER_STATUS_LABEL[s], `no label for ${s}`);
        assert.ok(ORDER_STATUS_TONE[s], `no tone for ${s}`);
    }
});

test("a pending order states uncertainty rather than proving no charge", () => {
    // A pending local row does not establish whether the provider accepted money.
    assert.equal(ORDER_STATUS_LABEL.pending, "Payment unconfirmed");
});

// ── PayPal top-ups ──
import { billingConfigSchema, paypalCaptureSchema, paypalCheckoutSchema } from "@/lib/topup-api";

const PAYPAL_ORDER = "00000000-0000-4000-8000-000000000404";

test("the billing config parses with the PayPal fields and treats a missing field as closed", () => {
    const open = billingConfigSchema.parse({ topups_open: true, paypal_topups_open: true, paypal_topups_unavailable_reason: null });
    assert.equal(open.paypal_topups_open, true);
    const older = billingConfigSchema.parse({ topups_open: true });
    assert.notEqual(older.paypal_topups_open, true);
    assert.equal(older.paypal_topups_unavailable_reason ?? null, null);
    const closed = billingConfigSchema.parse({ topups_open: true, paypal_topups_open: false, paypal_topups_unavailable_reason: "Closed for now." });
    assert.equal(closed.paypal_topups_open, false);
    assert.equal(closed.paypal_topups_unavailable_reason, "Closed for now.");
});

test("a malformed PayPal config field does not fail the whole config and reads as closed", () => {
    const parsed = billingConfigSchema.parse({ topups_open: true, paypal_topups_open: "yes", paypal_topups_unavailable_reason: 7 });
    assert.equal(parsed.topups_open, true);
    assert.notEqual(parsed.paypal_topups_open, true);
});

test("the PayPal checkout answer parses", () => {
    const parsed = paypalCheckoutSchema.parse({ order_id: PAYPAL_ORDER, paypal_order_id: "5O190127TN364715T",
        checkout_url: "https://www.paypal.com/checkoutnow?token=5O190127TN364715T", minutes: 250, price_cents: 2500, currency: "gbp" });
    assert.equal(parsed.minutes, 250);
    assert.throws(() => paypalCheckoutSchema.parse({ order_id: "nope", paypal_order_id: "x", checkout_url: "https://www.paypal.com/", minutes: 1, price_cents: 1, currency: "gbp" }));
});

test("the PayPal capture answer parses each status and refuses an unknown one", () => {
    for (const status of ["paid", "pending", "not_approved", "failed"] as const) {
        assert.equal(paypalCaptureSchema.parse({ order_id: PAYPAL_ORDER, status, minutes: status === "paid" ? 250 : null }).status, status);
    }
    assert.throws(() => paypalCaptureSchema.parse({ order_id: PAYPAL_ORDER, status: "refunded", minutes: null }));
});
