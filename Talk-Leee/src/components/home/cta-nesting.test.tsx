import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { ThemeProvider } from "@/components/providers/theme-provider";
import { AuthProvider } from "@/lib/auth-context";
import { apiBaseUrl, env } from "@/lib/env";
import { CTASection } from "./cta-section";
import HealthcarePage from "@/app/industries/healthcare/page";
import PricingPage from "@/app/pricing/page";
import EducationPage from "@/app/industries/education/page";

const originalApiBase = env.NEXT_PUBLIC_API_BASE_URL;
const syntheticApiBase = "https://catalog.example.invalid/api/v1";
const paygBundle = { code: "payg_125", minutes: 125, price_cents: 5000, currency: "usd", currency_exponent: 2 };
afterEach(() => { cleanup(); env.NEXT_PUBLIC_API_BASE_URL = originalApiBase; });

// The marketing navbar calls usePathname(), useRouter() and useAuth(); supply
// the contexts exactly as ai-voices/page.test.tsx does. Real components stay
// mounted; the public PAYG catalogue read has an exact synthetic fixture below.
function renderPublic(pathname: string, ui: React.ReactElement) {
    const router = {
        push: () => {},
        replace: () => {},
        refresh: () => {},
        back: () => {},
        forward: () => {},
        prefetch: async () => {},
    } as unknown as React.ContextType<typeof AppRouterContext>;
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

const BUTTON_BASE = ["inline-flex", "items-center", "justify-center"];

const cases: Array<[string, React.ComponentType, number]> = [
    ["/industries/healthcare/", HealthcarePage, 9],
    ["/industries/education/", EducationPage, 9],
    ["/pricing/", PricingPage, 4],
    ["/", CTASection, 1],
];

for (const [pathname, Page, expectedCtas] of cases) {
    test(`${pathname}: no link contains a button and each CTA is one styled link`, async (context) => {
        env.NEXT_PUBLIC_API_BASE_URL = syntheticApiBase;
        assert.equal(apiBaseUrl(), syntheticApiBase);
        const catalogRequests: Array<Parameters<typeof fetch>> = [];
        // Pricing's real PAYG child remains mounted. Admit only its exact,
        // synthetic read; unknown requests fail and never reach a network.
        context.mock.method(globalThis, "fetch", async (...args: Parameters<typeof fetch>) => {
            catalogRequests.push(args);
            assert.deepEqual(args, [`${syntheticApiBase}/plans/payg`]);
            assert.equal(args[1]?.method ?? "GET", "GET");
            assert.equal(args[1]?.body, undefined);
            // Same valid plan/package shape as pay-as-you-go.test.tsx.
            return new Response(JSON.stringify({ plan: { id: "payg" }, packages: [paygBundle] }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
            });
        });
        let rendered!: ReturnType<typeof renderPublic>;
        await act(async () => { rendered = renderPublic(pathname, <Page />); });
        const { container } = rendered;
        assert.equal(container.querySelectorAll("a button").length, 0, "anchor containing a button");
        assert.equal(container.querySelectorAll("button a").length, 0, "button containing an anchor");
        // The former <Link><Button> pairs now render as one <a> carrying the
        // Button classes, so the appearance is unchanged.
        const styledLinks = [...container.querySelectorAll("a")].filter((a) =>
            BUTTON_BASE.every((c) => a.classList.contains(c)),
        );
        assert.equal(styledLinks.length, expectedCtas);
        for (const a of styledLinks) {
            assert.ok(a.getAttribute("href"), "CTA link has a destination");
            assert.ok(a.textContent?.trim(), "CTA link has an accessible name");
        }
        assert.deepEqual(
            catalogRequests,
            pathname === "/pricing/" ? [[`${syntheticApiBase}/plans/payg`]] : [],
            "Only Pricing may request its public catalogue, exactly once with no options",
        );
    });
}
