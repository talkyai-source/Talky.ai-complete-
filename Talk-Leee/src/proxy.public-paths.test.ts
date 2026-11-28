import assert from "node:assert/strict";
import { test } from "node:test";
import { isPublicPath } from "@/proxy";

// For a signed-out visitor the fail-open branch of the proxy returns the same
// response as the public-path branch, so a request alone cannot tell "listed as
// public" from "let through by fail-open". These tests ask the list itself.

test("T8: the legal pages are public by the list, in both slash forms", () => {
    for (const p of ["/privacy", "/privacy/", "/terms", "/terms/", "/accessibility", "/accessibility/"]) {
        assert.equal(isPublicPath(p), true, `${p} is not in the public list`);
    }
});

test("T9: signed-in pages and look-alike addresses are still not on the public list", () => {
    for (const p of ["/dashboard/", "/billing/", "/calls/", "/privacy-admin/", "/termsheet/", "/accessibility-x/"]) {
        assert.equal(isPublicPath(p), false, `${p} must not be public`);
    }
});
