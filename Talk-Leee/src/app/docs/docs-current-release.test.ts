import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { docsPages, findDocsPage } from "./_content/registry";

const directory = path.join(process.cwd(), "src/app/docs/_content");
const content = (name: string) => fs.readFileSync(path.join(directory, name), "utf8");

for (const slug of ["support", "partners"]) {
    test(`${slug} published page is an honest availability notice while catalog withdrawal is owner-blocked`, () => {
        const page = findDocsPage(slug);
        assert.ok(page, "Do not silently claim that the protected publication catalog has been changed");
        assert.match(page.description, /availability|not (?:available|enabled)/i);
        const text = content(page.file);
        assert.match(text, slug === "support" ? /(?:not enabled|disabled) by default/i : /(?:not available|unavailable) in this release/i);
        assert.match(text, /contact.*(?:owner|form)|(?:owner|administrator).*contact/i);
        assert.doesNotMatch(text, /Select \*\*Help\*\* to open|## Manage tenants|## Partner dashboard|### How to follow up/i);
    });
}

test("enabled core documentation remains available after withholding optional workflows", () => {
    for (const slug of ["getting-started", "billing", "troubleshooting", "settings-and-security",
        "outbound-campaigns", "contacts", "calls-and-recordings", "ai-options"]) {
        assert.ok(findDocsPage(slug), `Do not hide ${slug} to avoid correcting its content`);
    }
});

test("getting started describes current paid choices without an unavailable trial or admin workflow", () => {
    const text = content("getting-started.md");
    assert.doesNotMatch(text, /Start Free Trial|Developer Hub|white-label partner areas/i);
    assert.match(text, /Get Started/);
    assert.match(text, /pay.as.you.go/i);
    assert.match(text, /original (?:page|link)|page you (?:requested|were trying to open)/i);
});

for (const name of ["getting-started.md", "billing.md"]) {
    test(`${name} does not promise implemented separate carrier billing`, () => {
        const text = content(name);
        assert.doesNotMatch(text, /carrier[^\n]*(?:are|remain|may be) billed separately/i);
        assert.match(text, /(?:confirm|ask)[^\n]*carrier|carrier[^\n]*(?:owner|provider)/i);
    });
}

test("billing guides both subscription and pay-as-you-go customers using the current catalog", () => {
    const text = content("billing.md");
    assert.match(text, /pay.as.you.go/i);
    assert.match(text, /current (?:bundles|prices|catalog)|prices shown/i);
    assert.doesNotMatch(text, /\|\s*\$50\s*\|\s*125|\|\s*\$70\s*\|\s*175|\|\s*\$100\s*\|\s*250/);
    assert.match(text, /payment is verified|payment confirmation/i);
});

test("troubleshooting offers the contact fallback without advertising a hidden support workflow", () => {
    const text = content("troubleshooting.md");
    assert.match(text, /contact form/i);
    assert.doesNotMatch(text, /Select the \*\*Help\*\* button|Follow up under \*\*Support\*\*|\]\(\/docs\/support\)/i);
    assert.match(text, /Do not include passwords, API keys/i);
});

test("published account-security help does not instruct customers to use unavailable developer controls", () => {
    const text = content("settings-and-security.md");
    assert.doesNotMatch(text, /Developer Hub|Register the endpoint that should receive events/i);
    assert.match(text, /Two-factor authentication/i);
    assert.match(text, /Settings.*Telephony/i);
    assert.doesNotMatch(findDocsPage("settings-and-security")!.description, /Developer Hub/i);
});

test("published docs do not link to the withheld feature pages", () => {
    for (const page of docsPages) {
        assert.doesNotMatch(content(page.file), /\]\(\/docs\/(?:support|partners)(?:[\/#?)]|$)/,
            `${page.file} must not send readers to a withheld page`);
    }
});
