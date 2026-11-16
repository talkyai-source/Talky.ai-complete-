/**
 * FX6a Task 3 (checklist: "No page shows a feature, plan or status that does
 * not exist in the backend."). The travel page's FAQ said the platform
 * connects with property management systems and booking platforms; the server
 * has no such connector, so the whole item (question and answer) is removed.
 * The other FAQ items stay.
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

/** The FAQ questions, in page order. */
function faqQuestions(): string[] {
    const heading = screen.getByRole("heading", { name: "Frequently Asked Questions" });
    const section = heading.closest("section");
    assert.equal(section !== null, true, "the FAQ section is on the page");
    return Array.from(section!.querySelectorAll("h3")).map((node) => node.textContent ?? "");
}

test("the hotel-management-software FAQ item is gone: question and answer", (context) => {
    context.mock.method(globalThis, "fetch", async () => { throw new Error("no network in this test"); });
    renderPage();
    const text = document.body.textContent ?? "";
    assert.equal(screen.queryByRole("heading", { name: "Does it integrate with our hotel management software?" }) === null, true, "the question is removed");
    assert.equal(/hotel management software/i.test(text), false);
    assert.equal(/leading property management systems/i.test(text), false, "the answer is removed");
    assert.equal(/keep guest information synchronized/i.test(text), false, "the answer is removed with its question");
});

test("the other FAQ items stay, in their order", (context) => {
    context.mock.method(globalThis, "fetch", async () => { throw new Error("no network in this test"); });
    renderPage();
    const questions = faqQuestions();
    const receptionist = questions.indexOf("Can AI act as a virtual receptionist for hotels?");
    const started = questions.indexOf("How quickly can we get started?");
    assert.equal(receptionist >= 0, true, "the item before the removed one stays");
    assert.equal(started, receptionist + 1, "the item after it now follows directly");
    assert.equal(new Set(questions).size, questions.length, "no question is listed twice");
    assert.equal(questions.every((question) => question.trim().length > 0), true, "no empty FAQ card is left behind");
});
