import assert from "node:assert/strict";
import { test } from "node:test";
import { isAllowedSupportLink, sanitizeSupportMarkdown, supportSourceHref } from "@/lib/support-links";

test("only single-slash in-app paths are allowed", () => {
    assert.equal(isAllowedSupportLink("/docs/billing#top-ups"), true);
    assert.equal(isAllowedSupportLink("/campaigns/abc"), true);
    assert.equal(isAllowedSupportLink("//evil.example"), false);
    assert.equal(isAllowedSupportLink("https://evil.example"), false);
    assert.equal(isAllowedSupportLink("javascript:alert(1)"), false);
    assert.equal(isAllowedSupportLink("mailto:a@b.c"), false);
    assert.equal(isAllowedSupportLink("/with space"), false);
});

test("in-app markdown links survive, external ones become their text", () => {
    const input = "See [Billing](/docs/billing) and [the site](https://example.com/x \"t\") or [here](//cdn.example).";
    assert.equal(sanitizeSupportMarkdown(input), "See [Billing](/docs/billing) and the site or here.");
});

test("bare and angle-bracket URLs are rendered as code so remark-gfm cannot autolink them", () => {
    assert.equal(sanitizeSupportMarkdown("Go to https://example.com/path now"), "Go to `https://example.com/path` now");
    assert.equal(sanitizeSupportMarkdown("Try <https://example.com>"), "Try `https://example.com`");
    assert.equal(sanitizeSupportMarkdown("www.example.com works"), "`www.example.com` works");
    assert.equal(sanitizeSupportMarkdown("(see https://a.b)"), "(see `https://a.b`)");
});

test("images are reduced to their alt text", () => {
    assert.equal(sanitizeSupportMarkdown("Look ![a chart](https://img.example/c.png) here"), "Look a chart here");
    assert.equal(sanitizeSupportMarkdown("![](/local.png)"), "");
});

test("plain text and in-app content pass through unchanged", () => {
    const text = "Open **Settings → Telephony** and select Check again.\n\n- one\n- two";
    assert.equal(sanitizeSupportMarkdown(text), text);
});

test("source chips link only into the docs", () => {
    assert.equal(supportSourceHref("/docs/billing#invoices"), "/docs/billing#invoices");
    assert.equal(supportSourceHref("/docs"), "/docs");
    assert.equal(supportSourceHref("/support/articles/refund-policy"), null);
    assert.equal(supportSourceHref("https://docs.example"), null);
});
