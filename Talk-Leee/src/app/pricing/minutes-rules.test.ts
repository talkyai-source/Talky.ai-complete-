import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

// QA F13-13 (QA-BIL-011): the pricing page did not say how minutes are
// counted or when a plan's minutes renew. Both now match the meter
// (backend minutes_quota: monthly from the purchase; seconds summed, floored).
const page = readFileSync(path.join(process.cwd(), "src/app/pricing/page.tsx"), "utf8");

test("the pricing page says plan minutes renew monthly from the subscription date", () => {
    assert.match(page, /monthly AI minutes renew each month from the date you subscribe/);
});

test("the pricing page states the rounding rule the meter applies", () => {
    assert.match(page, /added up over that month and shown in whole minutes, rounded down/);
});
