import assert from "node:assert/strict";
import Module from "node:module";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, mock, test } from "node:test";
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

// The copyright year comes from the clock, never from a typed number.
test("the copyright line shows the year from the clock", () => {
    mock.timers.enable({ apis: ["Date"], now: new Date("2031-06-15T12:00:00Z") });
    try {
        render(<Footer />);
        const line = Array.from(document.querySelectorAll("footer p")).find((p) => /All rights reserved/.test(p.textContent ?? ""));
        assert.ok(line, "the copyright line");
        assert.match(line.textContent ?? "", /© 2031 Talk-Lee\./);
    } finally {
        mock.timers.reset();
    }
});

test("footer.tsx types no year literal", () => {
    const source = readFileSync(path.join(process.cwd(), "src/components/home/footer.tsx"), "utf8");
    assert.equal(/20\d\d/.test(source), false);
});

// Routes that exist, read from the page files under src/app at run time (route
// groups dropped, [param] folders match any one segment).
function pageRoutes(dir: string, segments: string[] = []): string[][] {
    const out: string[][] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.isDirectory()) {
            const grouped = /^\(.*\)$/.test(entry.name);
            out.push(...pageRoutes(path.join(dir, entry.name), grouped ? segments : [...segments, entry.name]));
        } else if (/^page\.(tsx|ts|jsx|js|mdx)$/.test(entry.name)) {
            out.push(segments);
        }
    }
    return out;
}

function routeExists(href: string, routes: string[][]): boolean {
    const parts = href.split(/[?#]/)[0].split("/").filter(Boolean);
    return routes.some(
        (route) => route.length === parts.length && route.every((seg, i) => (/^\[.+\]$/.test(seg) ? true : seg === parts[i])),
    );
}

for (const status of ["anonymous", "authenticated"] as const) {
    test(`${status}: every internal footer link is an existing route`, () => {
        authStatus = status;
        render(<Footer />);
        const routes = pageRoutes(path.join(process.cwd(), "src/app"));
        const hrefs = Array.from(document.querySelectorAll("footer a[href]")).map((a) => a.getAttribute("href") ?? "");
        const internal = hrefs.filter((h) => h.startsWith("/"));
        assert.ok(internal.length >= 10, `expected the footer's links, got ${internal.length}`);
        for (const href of internal) assert.ok(routeExists(href, routes), `${href} has no page under src/app`);
    });
}
