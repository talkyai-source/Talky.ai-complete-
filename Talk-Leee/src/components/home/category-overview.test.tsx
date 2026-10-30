import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { ThemeProvider } from "@/components/providers/theme-provider";
import { AuthProvider } from "@/lib/auth-context";
import { industryNavItems } from "@/Industries/industries";
import { productNavItems, useCaseNavItems } from "@/lib/products-use-cases";
import ProductsPage, { metadata as productsMetadata } from "@/app/products/page";
import UseCasesPage, { metadata as useCasesMetadata } from "@/app/use-cases/page";
import IndustriesPage, { metadata as industriesMetadata } from "@/app/industries/page";

afterEach(cleanup);

// Same contexts as cta-nesting.test.tsx: the navbar calls usePathname(),
// useRouter() and useAuth().
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

// The shared modules (Industries/industries.ts, lib/products-use-cases.ts)
// are the single source of each category's items; the navbar dropdowns and these
// pages both read from them. Every label, href and description a page renders
// must equal that source, character for character, in the same order.
type Expected = { label: string; href: string; description: string };

const cases: Array<{
    pathname: string;
    Page: React.ComponentType;
    heading: string;
    title: unknown;
    expected: Expected[];
}> = [
    { pathname: "/products/", Page: ProductsPage, heading: "Products", title: productsMetadata.title, expected: [...productNavItems] },
    { pathname: "/use-cases/", Page: UseCasesPage, heading: "Use Cases", title: useCasesMetadata.title, expected: [...useCaseNavItems] },
    { pathname: "/industries/", Page: IndustriesPage, heading: "Industries", title: industriesMetadata.title, expected: [...industryNavItems] },
];

const expectedCounts: Record<string, number> = { "/products/": 3, "/use-cases/": 2, "/industries/": 10 };

for (const { pathname, Page, heading, title, expected } of cases) {
    test(`${pathname}: one card per item, in order, carrying the item's own label, description and link`, () => {
        assert.equal(expected.length, expectedCounts[pathname], "expected item count read from the source");
        const { container } = renderPublic(pathname, <Page />);

        const h1 = container.querySelectorAll("h1");
        assert.equal(h1.length, 1);
        assert.equal(h1[0].textContent, heading);
        // The heading is the footer's label for this category.
        assert.equal(title, `${heading} | Talk-Lee AI`);

        const cards = [...container.querySelectorAll("main > div > ul[role='list'] > li > a")];
        assert.equal(cards.length, expected.length);
        cards.forEach((card, i) => {
            assert.equal(card.getAttribute("href"), expected[i].href);
            assert.equal(card.querySelector("h2")?.textContent, expected[i].label);
            assert.equal(card.querySelector("p")?.textContent, expected[i].description);
            assert.equal(card.querySelectorAll("svg").length, 1, "one icon per card");
            assert.equal(card.querySelector("svg")?.getAttribute("aria-hidden"), "true");
        });

        assert.equal(container.querySelectorAll("a button").length, 0, "anchor containing a button");
        assert.equal(container.querySelectorAll("footer").length, 1);
        assert.equal(container.querySelectorAll("nav[aria-label='Primary']").length, 1);
    });
}

test("the pages set no description of their own, so the root layout's is inherited", () => {
    for (const m of [productsMetadata, useCasesMetadata, industriesMetadata]) {
        assert.equal(Object.hasOwn(m, "description"), false);
    }
});
