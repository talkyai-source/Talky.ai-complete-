/**
 * The Privacy Policy page as rendered: the sentence about the documents asked
 * for when a phone number is bought in Telephony, and the page's dates.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { PRIVACY_POLICY_DATES } from "@/lib/privacy-policy-dates";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/home/navbar")) return { Navbar: () => null };
    if (id.includes("components/home/footer")) return { Footer: () => React.createElement("footer") };
    return originalLoad.call(this, id, ...args);
};
let PrivacyPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    PrivacyPage = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(cleanup);

const DOCUMENTS = "We do not collect Social Security numbers. If you buy a phone number in Telephony, we ask for the documents that destination regulations require: for business use, your business registration and a photo ID matching it; for personal use, a utility bill as proof of address and a photo ID matching it.";

test("the privacy page says which documents are asked for when a phone number is bought in Telephony", () => {
    render(<PrivacyPage />);
    const item = screen.getByText(DOCUMENTS);
    assert.equal(item.tagName, "LI");
    assert.equal(document.body.textContent?.includes("national identity documents"), false);
});

/** "2026-12-20" -> "20 December 2026", worked out here without the code under test. */
const printed = (isoDate: string) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${isoDate}T00:00:00Z`));

test("the privacy page shows the shared effective date and last-updated date", () => {
    render(<PrivacyPage />);
    const dates = screen.getByText(/^Effective Date:/);
    assert.equal(dates.textContent?.replace(/\s+/g, " "), `Effective Date: ${printed(PRIVACY_POLICY_DATES.effectiveDate)} | Last Updated: ${printed(PRIVACY_POLICY_DATES.lastUpdated)}`);
    assert.notEqual(PRIVACY_POLICY_DATES.effectiveDate, PRIVACY_POLICY_DATES.lastUpdated);
});
