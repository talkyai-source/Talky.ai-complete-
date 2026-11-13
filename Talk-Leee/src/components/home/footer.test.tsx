import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

// The footer's two link columns are row subgrids that span a fixed number of
// the parent's tracks (row-span-N). A subgrid cannot add rows, so an item past
// the last spanned track is clamped into it and painted over its neighbour:
// seven links in a six-track column drew "Login" and "Get Started" on top of
// each other. Each test below fails when a link is added without raising N.
// Real Footer; only the auth context is stubbed so the test can drive the
// sign-in state the component sees.
let authStatus = "anonymous";
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("lib/auth-context")) {
        return { useAuth: () => ({ status: authStatus, user: authStatus === "authenticated" ? { role: "user" } : null }) };
    }
    return originalLoad.call(this, id, ...args);
};
let Footer: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    Footer = require("./footer").Footer;
} finally {
    loader._load = originalLoad;
}

afterEach(() => {
    cleanup();
    authStatus = "anonymous";
});

function rowSpan(el: Element): number {
    const match = /(?:^|\s)row-span-(\d+)(?:\s|$)/.exec(el.className);
    assert.ok(match, `expected a row-span-N class on <${el.tagName.toLowerCase()}>, got "${el.className}"`);
    return Number(match[1]);
}

function columns() {
    const accessibility = document.querySelector('footer a[href="/accessibility"]');
    assert.ok(accessibility, "the third column's Accessibility link");
    const third = accessibility.parentElement as HTMLElement;
    const navList = document.querySelector("footer nav ul");
    assert.ok(navList, "the nav list");
    return { third, nav: navList.parentElement as HTMLElement, navList };
}

for (const status of ["anonymous", "authenticated"] as const) {
    test(`${status}: the third column spans exactly as many rows as it has links`, () => {
        authStatus = status;
        render(<Footer />);
        const { third } = columns();
        const authLink = third.querySelector(status === "anonymous" ? 'a[href="/auth/login"]' : 'a[href="/dashboard"], a[href^="/dashboard"], a[href^="/admin"]');
        assert.ok(authLink, `the ${status === "anonymous" ? "Login" : "Dashboard"} link is in the third column`);
        assert.equal(
            rowSpan(third),
            third.children.length,
            `third column row-span must equal its ${third.children.length} children, or the extra links overlap`,
        );
    });

    test(`${status}: the nav list spans at least as many rows as it has links`, () => {
        authStatus = status;
        render(<Footer />);
        const { nav, navList } = columns();
        const links = navList.querySelectorAll("li").length;
        assert.ok(rowSpan(navList) >= links, `nav list row-span must be at least its ${links} links`);
        assert.equal(rowSpan(nav), rowSpan(navList), "the nav and its list span the same rows");
    });
}
