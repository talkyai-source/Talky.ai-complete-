import assert from "node:assert/strict";
import Module from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { isPublicRoute } from "@/lib/public-routes";

// QA F12-09: the accessibility statement, approved by the owner on 11 Nov 2026
// (reply within 5 working days), is published at /accessibility, linked from
// the footer and listed in the sitemap.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/home/navbar")) return { Navbar: () => null };
    if (id.includes("components/home/footer")) return { Footer: () => null };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(cleanup);

test("the statement names its target, claims no certification and lists the known gaps", () => {
    render(<Page />);
    assert.equal(screen.getAllByRole("heading", { level: 1 }).length, 1);
    assert.equal(screen.getAllByRole("heading", { level: 1, name: "Accessibility Statement" }).length, 1);
    assert.equal(screen.getAllByText(/\(WCAG\) 2\.2 at level AA/).length, 1);
    assert.equal(screen.getAllByText(/do not claim conformance or certification/).length, 1);
    assert.equal(screen.getAllByRole("heading", { level: 2, name: "Known gaps we are working on" }).length, 1);
    assert.equal(document.body.textContent?.includes("["), false, "a draft placeholder was published");
});

test("people can reach us from it, and know when we reply", () => {
    render(<Page />);
    assert.equal(screen.getByRole("link", { name: "contact form" }).getAttribute("href"), "/contact");
    assert.equal(screen.getAllByText(/reply within 5 working days/).length, 1);
});

test("the footer and the sitemap list it", () => {
    assert.equal(isPublicRoute("/accessibility"), true);
    const footer = readFileSync(path.join(process.cwd(), "src/components/home/footer.tsx"), "utf8");
    assert.match(footer, /href="\/accessibility"/);
});
