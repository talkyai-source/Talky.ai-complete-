/**
 * FX6c Task 0.3 (checklist: "No page shows a feature, plan or status that does
 * not exist in the backend."). FX6b removed the two integration cards with no
 * connector behind them; the section's intro still named "reservations, guest
 * information,". Those two items are removed from the intro, nothing else.
 */
import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { ThemeProvider } from "@/components/providers/theme-provider";
import { AuthProvider } from "@/lib/auth-context";
import TravelIndustryPage from "./page";

afterEach(cleanup);

test("the integrations intro no longer names reservations or guest information; the rest of the sentence is unchanged", (context) => {
    context.mock.method(globalThis, "fetch", async () => { throw new Error("no network in this test"); });
    const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
    render(
        <AppRouterContext.Provider value={router}>
            <PathnameContext.Provider value="/industries/travel-industry/">
                <AuthProvider>
                    <ThemeProvider>
                        <TravelIndustryPage />
                    </ThemeProvider>
                </AuthProvider>
            </PathnameContext.Provider>
        </AppRouterContext.Provider>,
    );
    const heading = screen.getByRole("heading", { name: "Connect the Tools Talk-Lee AI Supports" });
    const intro = heading.nextElementSibling;
    assert.equal(intro?.tagName, "P");
    assert.equal(
        (intro?.textContent ?? "").replace(/\s+/g, " ").trim(),
        "Connect HubSpot or Salesforce to log calls, and Gmail to send follow-up emails.",
    );
    // The section keeps its eyebrow, heading and the two cards FX6b left.
    const section = heading.closest("section")!;
    assert.equal(section.querySelector("p")?.textContent, "Connectors");
    assert.deepEqual(Array.from(section.querySelectorAll("h3")).map((node) => node.textContent), ["Call Logging", "Follow-up Email"]);
});
