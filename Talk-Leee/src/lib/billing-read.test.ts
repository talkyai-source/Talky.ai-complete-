import { test } from "node:test";
import assert from "node:assert/strict";
import { formatMinorMoney } from "@/lib/billing-read";

test("invoice money respects zero-, two- and three-decimal currencies", () => {
  assert.equal(formatMinorMoney(1000, "JPY", 0), "¥1,000");
  assert.equal(formatMinorMoney(2500, "GBP", 2), "£25.00");
  assert.equal(formatMinorMoney(1234, "KWD", 3), "KWD 1.234");
});

test("unknown invoice amounts are unavailable; recorded zero and negative credits stay real amounts", () => {
  assert.equal(formatMinorMoney(null, "GBP", 2), "Unavailable");
  assert.equal(formatMinorMoney(2500, null, 2), "Unavailable");
  assert.equal(formatMinorMoney(2500, "GBP", null), "Unavailable");
  assert.equal(formatMinorMoney(0, "GBP", 2), "£0.00");
  assert.equal(formatMinorMoney(-5, "GBP", 2), "-£0.05");
});

test("safe minor integers retain every minor unit and unsafe or fractional totals are rejected", () => {
  assert.equal(formatMinorMoney(Number.MAX_SAFE_INTEGER, "GBP", 2), "£90,071,992,547,409.91");
  assert.equal(formatMinorMoney(Number.MAX_SAFE_INTEGER + 1, "GBP", 2), "Unavailable");
  assert.equal(formatMinorMoney(2.5, "GBP", 2), "Unavailable");
});
