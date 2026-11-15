/**
 * FX5 Task B3 — the industry pages and the home packages section show no plan
 * that does not exist (Starter / Growth / Enterprise cards) and name no
 * integration the server has no connector for. Links to /pricing stay.
 *
 * FX5b Tasks 2 and 3 — the same for the real-estate and software-tech-support
 * plan cards and for the remaining integration names with no connector.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test, { afterEach } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { ThemeProvider } from "@/components/providers/theme-provider";
import { AuthProvider } from "@/lib/auth-context";
import EducationPage from "./education/page";
import FinancialServicesPage from "./financial-services/page";
import HealthcarePage from "./healthcare/page";
import MarketingAutomationPage from "./marketing-automation/page";
import RealEstatePage from "./real-estate/page";
import RecruitmentPage from "./recruitment/page";
import SoftwareTechSupportPage from "./software-tech-support/page";
import TravelIndustryPage from "./travel-industry/page";

afterEach(cleanup);

function renderPublic(pathname: string, ui: React.ReactElement) {
    const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
    return render(
        <AppRouterContext.Provider value={router}>
            <PathnameContext.Provider value={pathname}>
                <AuthProvider>
                    <ThemeProvider>{ui}</ThemeProvider>
                </AuthProvider>
            </PathnameContext.Provider>
        </AppRouterContext.Provider>,
    );
}

/** The page's own content, without the shared navbar and footer. */
function mainText() {
    const clone = document.body.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("nav, footer, header").forEach((node) => node.remove());
    return clone.textContent ?? "";
}

const REMOVED_INTEGRATIONS = /Zapier|Workday|Greenhouse|GoHighLevel|Microsoft Dynamics|Lever|BambooHR|Oracle OPERA|Cloudbeds|Mews|Calendly|Cal.com|Apple Calendar/;
// [route, page, plan-section heading that went with the cards, a /pricing link was on the page]
const pages: Array<[string, React.ComponentType, string, boolean]> = [
    ["/industries/education/", EducationPage, "Flexible Plans for Every Institution", false],
    ["/industries/financial-services/", FinancialServicesPage, "Pricing for Every Financial Business", true],
    ["/industries/healthcare/", HealthcarePage, "Flexible Plans for Every Healthcare Practice", false],
    ["/industries/marketing-automation/", MarketingAutomationPage, "Flexible Plans for Every Marketing Agency", true],
    ["/industries/real-estate/", RealEstatePage, "Flexible Plans for Real Estate Teams", false],
    ["/industries/recruitment/", RecruitmentPage, "Pricing for Every Hiring Team", true],
    ["/industries/software-tech-support/", SoftwareTechSupportPage, "Flexible Solutions for Every Stage of Growth", false],
    ["/industries/travel-industry/", TravelIndustryPage, "Pricing for Every Travel & Hospitality Business", true],
];

for (const [route, Page, planHeading, hadPricingLink] of pages) {
    test(`${route}: no plan cards, no leftover plan heading, no integration without a connector`, (context) => {
        context.mock.method(globalThis, "fetch", async () => { throw new Error("no network in this test"); });
        renderPublic(route, <Page />);
        for (const plan of ["Starter", "Growth", "Enterprise", "Practice", "Solo", "Team", "Launch", "Scale"]) {
            assert.equal(screen.queryByRole("heading", { name: plan }) === null, true, `no "${plan}" plan card`);
        }
        assert.equal(screen.queryByRole("heading", { name: planHeading }) === null, true, "the emptied section's heading is gone");
        assert.equal(/Custom Pricing|Let’s Talk|See Team Plans|See Growth Plans/.test(mainText()), false, "no plan price lines or plan buttons");
        assert.equal(REMOVED_INTEGRATIONS.test(mainText()), false);
        assert.equal(screen.queryByRole("heading", { name: "Frequently Asked Questions" }) !== null
            || screen.queryByRole("heading", { name: /Everything You.re Wondering, Answered/ }) !== null, true, "the rest of the page stays");
        const pricingLinks = Array.from(document.querySelectorAll("main a, section a")).filter((a) => /^\/pricing\/?$/.test(a.getAttribute("href") ?? ""));
        if (hadPricingLink) assert.equal(pricingLinks.length > 0, true, "the existing link to /pricing stays");
    });
}

test("integrations that stay are still named", (context) => {
    context.mock.method(globalThis, "fetch", async () => { throw new Error("no network in this test"); });
    renderPublic("/industries/financial-services/", <FinancialServicesPage />);
    for (const name of ["Salesforce", "HubSpot", "Google Calendar", "Outlook"]) assert.equal(mainText().includes(name), true, name);
    cleanup();
    renderPublic("/industries/recruitment/", <RecruitmentPage />);
    for (const name of ["Outlook", "Google Calendar"]) assert.equal(mainText().includes(name), true, name);
    cleanup();
    renderPublic("/industries/travel-industry/", <TravelIndustryPage />);
    for (const name of ["Outlook", "Google Calendar"]) assert.equal(mainText().includes(name), true, name);
});

// The section's scroll animation cannot mount in jsdom ("controls.start() should only be
// called after a component has mounted"), so this one is a guard over the copy; the
// rendered line is measured on the production build.
test("home packages: the calendar line names only a calendar the server connects to", () => {
    const source = fs.readFileSync(path.join(process.cwd(), "src", "components", "home", "packages-section.tsx"), "utf8");
    assert.equal(source.includes('"Works with Google Calendar"'), true);
    assert.equal(REMOVED_INTEGRATIONS.test(source), false);
});
