import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

// Real DocsSidebar; only next/navigation is stubbed so the current page is fixed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/docs/contacts" };
    return originalLoad.call(this, id, ...args);
};
let DocsSidebar: React.ComponentType<{ pages: { slug: string; title: string }[] }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    DocsSidebar = require("./docs-sidebar").DocsSidebar;
} finally {
    loader._load = originalLoad;
}

const pages = Array.from({ length: 14 }, (_, i) => ({ slug: i === 1 ? "contacts" : `page-${i}`, title: i === 1 ? "Contacts" : `Page ${i}` }));

afterEach(() => cleanup());

test("the lg sidebar keeps its place and scrolls within its own boundary like the dashboard sidebar", () => {
    const { container } = render(<DocsSidebar pages={pages} />);
    const aside = container.querySelector("aside[data-docs-sidebar]");
    assert.ok(aside, "aside[data-docs-sidebar] renders");
    const classes = aside.className.split(/\s+/);
    // Stays in place while the page scrolls, with its top at the shell's content top
    // (navbar height token + the layout's md:py-12), not a hard-coded offset.
    assert.ok(classes.includes("lg:sticky"), "sticky from lg");
    assert.ok(classes.includes("lg:top-[calc(var(--home-navbar-height)+3rem)]"), "top offset uses the navbar height token");
    assert.ok(!classes.includes("lg:top-4"), "the old fixed 16px offset is gone");
    // Height bound to the viewport below that top, so its bottom edge never passes the screen.
    assert.ok(classes.includes("lg:max-h-[calc(100vh-var(--home-navbar-height)-3rem)]"), "max height is the viewport less the top offset");
    // Same scrolling mechanism as layout/sidebar.tsx: overflow-y-auto with the scrollbar hidden.
    assert.ok(classes.includes("lg:overflow-y-auto"), "scrolls within itself when taller");
    assert.ok(classes.includes("hide-scrollbar"), "scrollbar hidden like the dashboard sidebar");
    // Reaching its own top or bottom must not scroll the page.
    assert.ok(classes.includes("lg:overscroll-y-contain"), "no scroll chaining to the page");
    // Only lg+ behaviour changed: nothing here applies below lg.
    for (const c of classes.filter((c) => /sticky|top-|max-h|overflow|overscroll/.test(c))) assert.ok(c.startsWith("lg:"), `${c} must be lg-scoped`);
    // Width, card and entries are untouched.
    assert.ok(classes.includes("hidden") && classes.includes("lg:block") && classes.includes("self-start"));
    const nav = aside.querySelector("nav");
    assert.equal(nav?.className, "rounded-xl border border-border/60 bg-card p-2");
    assert.equal(aside.querySelectorAll("a").length, 15);
    assert.equal(aside.querySelector("a[aria-current=page]")?.textContent, "Contacts");
});

test("the collapsible panel below lg is unchanged", () => {
    const { container } = render(<DocsSidebar pages={pages} />);
    const details = container.querySelector("details[data-docs-panel]");
    assert.ok(details, "details[data-docs-panel] renders");
    assert.equal(details.className, "lg:hidden rounded-xl border border-border/60 bg-card");
    assert.equal(details.hasAttribute("open"), false);
    assert.equal(details.querySelector("summary")?.textContent, "Documentation pages");
    assert.equal(details.querySelector("nav")?.className, "border-t border-border/60 p-2");
    assert.equal(details.querySelectorAll("a").length, 15);
});
