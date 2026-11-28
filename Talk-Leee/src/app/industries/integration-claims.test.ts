import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// A customer can connect exactly one calendar: Google Calendar (the Connectors page,
// backend DEFAULT_PROVIDER_BY_TYPE). Public pages must not name Outlook or promise
// "your own scheduling tools". A guard over the literal copy, since the defect is the copy.
const src = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (file: string) => readFileSync(path.join(src, file), "utf8");

const pages = [
    "components/home/home-lazy-sections.tsx",
    "app/industries/healthcare/page.tsx",
    "app/industries/marketing-automation/page.tsx",
    "app/industries/recruitment/page.tsx",
    "app/industries/financial-services/page.tsx",
    "app/industries/travel-industry/page.tsx",
    "app/industries/professional-services/page.tsx",
    "app/industries/retail-ecommerce/page.tsx",
];

for (const file of pages) {
    test(`${file} does not claim Outlook`, () => {
        assert.equal(/Outlook/i.test(read(file)), false, "only Google Calendar can be connected");
    });
}

test("recruitment and marketing do not promise other scheduling tools", () => {
    assert.equal(/your preferred scheduling tools/.test(read("app/industries/recruitment/page.tsx")), false);
    assert.equal(/existing scheduling tools/.test(read("app/industries/marketing-automation/page.tsx")), false);
});

// The same rule for systems nobody can connect: there is no connector for hotel
// (property management / booking) systems, applicant tracking or HR software.
test("no page promises a connection to systems that have no connector", () => {
    const travel = read("app/industries/travel-industry/page.tsx");
    assert.equal(/connects with leading property management systems/.test(travel), false);
    assert.equal(/booking platforms, calendars, and CRM solutions/.test(travel), false);
    const recruitment = read("app/industries/recruitment/page.tsx");
    assert.equal(/Connect your ATS, calendars, and HR software/.test(recruitment), false);
    const finance = read("app/industries/financial-services/page.tsx");
    assert.equal(/leading CRM platforms, scheduling tools, and business applications/.test(finance), false);
});

// W4-T10: no applicant tracking, core banking or help desk connector exists either. A page may
// name one only to say it is not available.
const NO_CONNECTOR = /\bATS\b|applicant tracking|core banking|help ?desk/i;
const NOT_AVAILABLE = /\b(?:no connector|not available|not connected|are not|is not|does not|cannot)\b/i;
for (const file of ["recruitment", "financial-services", "professional-services", "retail-ecommerce", "marketing-automation"].map((name) => `app/industries/${name}/page.tsx`)) {
    test(`${file} names no ATS, core banking or help desk system as a connection`, () => {
        const text = read(file).replace(/&(?:rsquo|apos);|’/g, "'").replace(/\s+/g, " ");
        const promises = text.split(/(?<=[.!?])\s+|[`"]/).filter((sentence) => NO_CONNECTOR.test(sentence) && !NOT_AVAILABLE.test(sentence));
        assert.deepEqual(promises, []);
        assert.equal(/\bintegrat(?:es|ion|ions)\b[^.]{0,60}\b(?:ATS|applicant tracking|core banking|help ?desk)/i.test(text), false);
    });
}
