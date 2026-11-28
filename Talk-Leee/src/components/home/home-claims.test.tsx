import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { FAQSection } from "./home-lazy-sections";
import { homeStructuredData, structuredDataJson } from "@/lib/structured-data";

afterEach(() => cleanup());

function answerTo(container: HTMLElement, question: string): string {
    const item = [...container.querySelectorAll("details")].find((d) => d.querySelector("summary")?.textContent === question);
    assert.ok(item, `FAQ question present: ${question}`);
    return item.querySelector("p")!.textContent ?? "";
}

test("W5-T9 the phone-number answer says a number can be requested in Settings, and that staff review it", () => {
    const { container } = render(<FAQSection />);
    const a = answerTo(container, "Do you provide phone numbers for campaigns?");
    assert.match(a, /Settings, Telephony/);
    assert.match(a, /Buy Now/);
    assert.match(a, /staff review/i);
    assert.match(a, /not an instant purchase/i);
    assert.doesNotMatch(a, /not available in this release/i);
});

test("W5-T9 the white-label answer is unchanged", () => {
    const { container } = render(<FAQSection />);
    assert.equal(
        answerTo(container, "Can I resell Talk-Lee AI under my own brand?"),
        "Not yet. A white‑label program is planned and is not available in this release. Contact us to register your interest.",
    );
});

test("W5-T10 the structured data repeats no claim removed from the home page", () => {
    const json = structuredDataJson(homeStructuredData);
    assert.doesNotMatch(json, /call records|campaign knowledge|integration|appointment|dedicated|A\/B|Conversation Intelligence|CCaaS|crawl|PDF/i);
    assert.match(json, /Call History/);
});

test("W5-T9 the knowledge answer names the real file types and no crawler", () => {
    const { container } = render(<FAQSection />);
    const a = answerTo(container, "How does Talk-Lee AI learn my company information?");
    assert.match(a, /\.md/);
    assert.match(a, /\.txt/);
    assert.match(a, /knowledge base/);
});
