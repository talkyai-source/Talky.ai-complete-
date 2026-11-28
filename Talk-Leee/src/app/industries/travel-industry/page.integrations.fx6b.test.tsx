/**
 * FX6b Task 0.2 (checklist: "No page shows a feature, plan or status that does
 * not exist in the backend."). The travel page's integrations section listed
 * "Property Management Systems" and "Booking Platforms"; the server has no
 * such connector, so both cards are removed. The section keeps its heading
 * because two cards are left in it.
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

function renderPage() {
    const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
    return render(
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
}

/** The integrations section: its card titles, in page order. */
function integrationCards(): string[] {
    const heading = screen.getByRole("heading", { name: "Connect the Tools Talk-Lee AI Supports" });
    const section = heading.closest("section");
    assert.equal(section !== null, true, "the integrations section is on the page");
    return Array.from(section!.querySelectorAll("h3")).map((node) => node.textContent ?? "");
}

test("the two cards with no connector behind them are gone, title and description", (context) => {
    context.mock.method(globalThis, "fetch", async () => { throw new Error("no network in this test"); });
    renderPage();
    const text = document.body.textContent ?? "";
    assert.equal(screen.queryByRole("heading", { name: "Property Management Systems" }) === null, true);
    assert.equal(screen.queryByRole("heading", { name: "Booking Platforms" }) === null, true);
    assert.equal(/with your existing PMS/i.test(text), false, "the first card's description is removed");
    assert.equal(/across your preferred booking systems/i.test(text), false, "the second card's description is removed");
});

test("the section keeps its heading and its other two cards, with no empty card left behind", (context) => {
    context.mock.method(globalThis, "fetch", async () => { throw new Error("no network in this test"); });
    renderPage();
    const cards = integrationCards();
    assert.deepEqual(cards, ["Call Logging", "Follow-up Email"]);
    const section = screen.getByRole("heading", { name: "Connect the Tools Talk-Lee AI Supports" }).closest("section")!;
    const grid = section.querySelector(".grid")!;
    assert.equal(grid.children.length, 2, "the grid holds exactly the two cards");
    assert.equal(Array.from(grid.children).every((card) => (card.textContent ?? "").trim().length > 0), true, "no empty card");
});
