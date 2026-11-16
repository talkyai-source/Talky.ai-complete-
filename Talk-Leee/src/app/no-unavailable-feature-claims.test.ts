import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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
