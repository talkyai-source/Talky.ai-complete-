import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Release review, 14 Nov 2026: public pages promised things the product does
// not have, and two legal statements contradicted it. Each entry names the
// claim, where it stood, and what the product really does. A guard over the
// literal copy, since the defect is the copy.
const src = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (file: string) => readFileSync(path.join(src, file), "utf8");

const claims: { file: string; claim: RegExp; why: string }[] = [
    { file: "components/home/features-section.tsx", claim: /Resell Talk-Lee AI under your own brand with dedicated access/i,
      why: "white-label is not available in this release (docs/_content/partners.md)" },
    { file: "components/home/home-lazy-sections.tsx", claim: /Yes\. Our white.label program lets you/i,
      why: "white-label is not available in this release" },
    { file: "components/home/home-lazy-sections.tsx", claim: /issues dedicated numbers/i,
      why: "there is no number provisioning; new accounts use the shared line or their own SIP trunk" },
    { file: "components/home/home-lazy-sections.tsx", claim: /lead forms/i,
      why: "there is no lead-form connector" },
    { file: "app/industries/healthcare/page.tsx", claim: /connect with most major scheduling and EHR platforms/i,
      why: "there is no EHR connector; bookings go to Google Calendar" },
    { file: "app/industries/marketing-automation/page.tsx", claim: /GoHighLevel|Zapier/,
      why: "the only CRM connectors are HubSpot and Salesforce" },
    { file: "app/pricing/page.tsx", claim: /Recording retention: 30 days|"Recording retention", "30 days"/,
      why: "retention comes from the account's recording policy (90 days by default), not from the plan" },
    { file: "app/pricing/page.tsx", claim: /CDRs \/ call detail records/,
      why: "customers get call history, not a call-detail-record export" },
    { file: "app/terms/terms-content.ts", claim: /All calls are recorded by default/i,
      why: "recording is off until the account sets a recording policy" },
    { file: "app/privacy/privacy-content.ts", claim: /never email body content|We do not read email body content|Gmail \(send only\)|files created by Talk-Lee only|Files you did not create through Talk-Lee/i,
      why: "the Gmail and Drive connectors request read access and the assistant reads messages and files on request" },
];

for (const { file, claim, why } of claims) {
    test(`${file} does not claim ${claim}`, () => {
        assert.equal(read(file).match(claim), null, why);
    });
}

test("the privacy page says what the Gmail and Drive connections can read", () => {
    const privacy = read("app/privacy/privacy-content.ts");
    assert.match(privacy, /read your messages, including their content, when you ask the assistant/);
    assert.match(privacy, /list and read the files in your Drive, including Google Docs, Sheets and Slides, when you ask the assistant/);
});

// W5 (28 Nov 2026): the home page promised things the product does not have.
// The surface is the page, its components and its structured data. footer.tsx,
// contact-section.tsx and category-overview.tsx belong to other packages and
// are read by their own tests.
const homeSurface = (): { file: string; text: string }[] => {
    const dir = path.join(src, "components/home");
    const files = [
        "app/page.tsx",
        "lib/structured-data.ts",
        ...readdirSync(dir)
            .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
            .filter((f) => !["footer.tsx", "contact-section.tsx", "category-overview.tsx"].includes(f))
            .map((f) => `components/home/${f}`),
    ];
    return files.map((file) => ({ file, text: read(file) }));
};

const home: { name: string; claim: RegExp; why: string }[] = [
    { name: "W5-T5 calendar booking by the voice agent", claim: /book(ing)? appointments?|appointment (booking|scheduling)|live calendars?|Works with Google Calendar/i,
      why: "calls never book into a calendar; only the dashboard assistant does" },
    { name: "W5-T5 knowledge import", claim: /\bPDFs?\b|(?<![/\w])images(?![/\w])|crawl/i,
      why: "the knowledge upload takes .md and .txt files only; nothing crawls websites" },
    { name: "W5-T5 forms as a connector", claim: /HubSpot\/forms|lead forms|connect with [^."]*forms/i,
      why: "there is no forms connector" },
    { name: "W5-T5 dedicated infrastructure", claim: /Your servers|Your GPUs|dedicated infrastructure|dedicated servers/i,
      why: "no such offering exists" },
    { name: "W5-T5 features the dashboard lacks", claim: /A\/B testing|Automated QA|Conversation Intelligence|drag.and.drop|CCaaS|all major CRM|call center software|real.time customer data/i,
      why: "none of these exists; Agent Quality is admin-only human review" },
    { name: "W5-T6 'integration' as the product's noun", claim: /\bintegrations?\b/i,
      why: "the dashboard says connector(s)" },
    { name: "W5-T6 'call records'", claim: /call records/i, why: "the dashboard says Call History" },
    { name: "W5-T6 'Knowledgebase' / 'campaign knowledge'", claim: /Knowledgebase|campaign knowledge/i,
      why: "the dashboard says knowledge base" },
];

for (const { name, claim, why } of home) {
    test(`${name}: absent from the home page and its components`, () => {
        for (const { file, text } of homeSurface()) {
            assert.equal(text.match(claim), null, `${file}: ${why}`);
        }
    });
}

// One line at a time: a template literal holding quotes (the hero's SVG) would
// otherwise shift the pairing for the rest of the file.
const literals = (text: string) =>
    text
        .split("\n")
        .flatMap((line) => [...line.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]))
        .filter((s) => s.includes(" ") && !s.trim().endsWith("?"));

test("W5-T7 every transfer sentence on the home page names its direction, its condition and that an answer is not guaranteed", () => {
    let seen = 0;
    for (const { file, text } of homeSurface()) {
        for (const s of literals(text).filter((l) => /\btransfer/i.test(l))) {
            seen += 1;
            assert.match(s, /outgoing|outbound/i, `${file}: no direction (outgoing): ${s}`);
            assert.match(s, /incoming|inbound/i, `${file}: no direction (incoming): ${s}`);
            assert.match(s, /set up|set-up|approved|needs|must/i, `${file}: no condition: ${s}`);
            assert.match(s, /not guaranteed|does not guarantee/i, `${file}: may promise that a person answers: ${s}`);
        }
    }
    assert.ok(seen >= 2, "the home page still describes transfer (card and FAQ)");
});

test("W5-T7 callbacks on the home page are outgoing only, and incoming calls note the request", () => {
    for (const { file, text } of homeSurface()) {
        for (const s of literals(text).filter((l) => /\bcallbacks?\b/i.test(l))) {
            assert.match(s, /outgoing|outbound|note|notes/i, `${file}: callback without its direction: ${s}`);
        }
    }
});
