import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// Browser overflow checks prove the layout; these guards prevent removal of
// the constraints on the same free-form fields that reproduced the failure.
const source = readFileSync(new URL("../../app/inbound-campaigns/page.tsx", import.meta.url), "utf8");

test("inbound route errors wrap rather than cover neighboring cards", () => {
    const error = source.match(/<p[^>]+>\{campaign\.last_error\}<\/p>/)?.[0];
    assert.ok(error);
    assert.match(error, /\[overflow-wrap:anywhere\]/);
});

test("tenant control references wrap while the action keeps its width", () => {
    const reason = source.match(/<p[^>]+>Last reason: \{tenantControls\.data\.reason\}<\/p>/)?.[0];
    assert.ok(reason);
    assert.match(reason, /\[overflow-wrap:anywhere\]/);
    assert.match(source, /<Button type="button" className="shrink-0" variant=\{tenantControls/);
});
