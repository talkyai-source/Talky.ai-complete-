import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/home/navbar")) return { Navbar: () => null };
    if (id.includes("components/home/footer")) return { Footer: () => React.createElement("footer") };
    return originalLoad.call(this, id, ...args);
};
let LegalDocument: React.ComponentType<{ title: string; effectiveDate: string; lastUpdated: string; blocks: [] }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    LegalDocument = require("./legal-document").LegalDocument;
} finally {
    loader._load = originalLoad;
}

afterEach(cleanup);

test("the legal page main area fills at least one screen above the footer", () => {
    const { container } = render(<LegalDocument title="T" effectiveDate="D" lastUpdated="D" blocks={[]} />);
    const main = container.querySelector("main");
    assert.ok(main);
    assert.ok(main.classList.contains("min-h-[calc(100svh-var(--home-navbar-height))]"), main.className);
    assert.equal(main.nextElementSibling?.tagName, "FOOTER");
});
