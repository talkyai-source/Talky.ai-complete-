import { test } from "node:test";
import assert from "node:assert/strict";
import { buildResponsiveHtmlDocument, isValidEmail, normalizeEmailList, splitEmailInput } from "@/lib/email-utils";

test("splitEmailInput splits by whitespace, commas, semicolons, and newlines", () => {
    const out = splitEmailInput(" a@x.com, b@x.com;\n c@x.com\t d@x.com  ");
    assert.deepEqual(out, ["a@x.com", "b@x.com", "c@x.com", "d@x.com"]);
});

test("isValidEmail accepts simple valid addresses and rejects invalid ones", () => {
    assert.equal(isValidEmail("a@b.com"), true);
    assert.equal(isValidEmail("a+b@b.com"), true);
    assert.equal(isValidEmail("no-at-symbol"), false);
    assert.equal(isValidEmail("a@b"), false);
    assert.equal(isValidEmail("a@b."), false);
});

test("normalizeEmailList trims and de-duplicates case-insensitively", () => {
    const out = normalizeEmailList([" A@x.com ", "a@x.com", "b@x.com", "B@x.com", "", "   "]);
    assert.deepEqual(out, ["A@x.com", "b@x.com"]);
});

test("buildResponsiveHtmlDocument wraps fragments and injects viewport meta", () => {
    const out = buildResponsiveHtmlDocument("<div>Hello</div>");
    assert.match(out, /<html[\s>]/i);
    assert.match(out, /<head[\s>]/i);
    assert.match(out, /name=["']viewport["']/i);
    assert.match(out, /<body>.*<div>Hello<\/div>.*<\/body>/i);
});

test("buildResponsiveHtmlDocument injects viewport into existing head", () => {
    const out = buildResponsiveHtmlDocument("<html><head><title>x</title></head><body>ok</body></html>");
    assert.match(out, /<head>.*<meta[^>]*name=["']viewport["'][^>]*>.*<title>x<\/title>/i);
});

test("buildResponsiveHtmlDocument does not duplicate viewport meta", () => {
    const input = `<html><head><meta name="viewport" content="width=device-width" /></head><body>ok</body></html>`;
    const out = buildResponsiveHtmlDocument(input);
    const matches = out.match(/name=["']viewport["']/gi) ?? [];
    assert.equal(matches.length, 1);
});

