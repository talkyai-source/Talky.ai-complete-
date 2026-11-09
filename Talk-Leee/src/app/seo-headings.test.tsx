import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { ContactSection } from "@/components/home/contact-section";
import UnauthorizedPage from "@/app/403/page";
import { metadata as loginMetadata } from "@/app/auth/login/page";
import { metadata as registerMetadata } from "@/app/auth/register/page";
import { metadata as forgotMetadata } from "@/app/auth/forgot-password/layout";
import { homeStructuredData, structuredDataJson } from "@/lib/structured-data";
import { SITE_URL } from "@/lib/site-metadata";

// QA F1-10 (QA-UI-203, QA-UI-166, QA-UI-200): the home page had two <h1>,
// /contact and /403 had none, the auth pages shared the title "Talk-Lee",
// and no page carried structured data.
afterEach(() => cleanup());

test("the home hero has exactly one <h1> (its mobile and desktop forms share it)", () => {
    const hero = readFileSync(path.join(process.cwd(), "src/components/ui/helix-hero.tsx"), "utf8");
    assert.equal((hero.match(/<h1\b/g) ?? []).length, 1);
});

test("/contact's heading is its one level-1 heading", () => {
    render(React.createElement(ContactSection));
    assert.ok(screen.getByRole("heading", { level: 1, name: "Contact Us" }));
});

test("/403 has a level-1 heading", () => {
    render(<UnauthorizedPage />);
    assert.ok(screen.getByRole("heading", { level: 1, name: "403 Unauthorized" }));
});

test("each auth page has its own title", () => {
    const titles = [loginMetadata.title, registerMetadata.title, forgotMetadata.title];
    assert.deepEqual(titles, ["Sign in | Talk-Lee", "Create your account | Talk-Lee", "Reset your password | Talk-Lee"]);
});

test("the home page describes the organisation and the product as structured data", () => {
    const graph = homeStructuredData["@graph"];
    const organization = graph.find((node) => node["@type"] === "Organization");
    const product = graph.find((node) => node["@type"] === "SoftwareApplication");
    assert.equal(organization?.url, `${SITE_URL}/`);
    assert.equal(organization?.name, "Talk-Lee");
    assert.equal(product?.url, `${SITE_URL}/`);
    assert.ok(!("offers" in (product ?? {})), "no price is claimed in markup");
    const json = structuredDataJson(homeStructuredData);
    assert.deepEqual(JSON.parse(json), homeStructuredData);
    assert.equal(structuredDataJson({ text: "</script><b>" }).includes("</script>"), false, "markup cannot close the script");
    const page = readFileSync(path.join(process.cwd(), "src/app/page.tsx"), "utf8");
    assert.match(page, /type="application\/ld\+json"/);
});
