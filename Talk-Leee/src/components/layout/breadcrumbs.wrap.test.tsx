/**
 * MERGE2 Task 7.2 — a breadcrumb that wraps keeps its rows apart. Each link
 * reaches 4px above and below its text (py-1 -my-1), so two wrapped rows need
 * 8px between them; with the old 4px the links overlapped by 4px at 360 and 390.
 * jsdom has no layout: this guards the spacing class, the production-build
 * measurement proves the result.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/campaigns/camp-1/edit" };
    if (id === "next/link") {
        return { __esModule: true, default: ({ href, children, className }: { href: string; children: React.ReactNode; className?: string }) => <a href={href} className={className}>{children}</a> };
    }
    return originalLoad.call(this, id, ...args);
};
let Breadcrumbs: React.ComponentType<{ labels?: Record<string, string> }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Breadcrumbs = require("./breadcrumbs").Breadcrumbs;
} finally {
    loader._load = originalLoad;
}

afterEach(cleanup);

test("wrapped breadcrumb rows are 8px apart, twice the 4px each link reaches past its text", () => {
    render(<Breadcrumbs labels={{ "camp-1": "Autumn renewal outreach 1" }} />);
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    const classes = nav.className.split(/\s+/);
    assert.equal(classes.includes("flex-wrap"), true);
    assert.equal(classes.includes("gap-y-2"), true, "8px between wrapped rows");
    assert.equal(classes.includes("gap-x-1"), true, "the 4px between crumbs on one row is unchanged");
    assert.equal(classes.includes("gap-1"), false);
    const link = screen.getByRole("link", { name: "Autumn renewal outreach 1" });
    assert.equal(link.className.includes("py-1") && link.className.includes("-my-1"), true, "the link still reaches 4px past its text");
});
