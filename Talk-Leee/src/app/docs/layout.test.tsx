import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/docs" };
    if (id.includes("components/home/navbar")) return { Navbar: () => null };
    if (id.includes("components/home/footer")) return { Footer: () => React.createElement("footer") };
    return originalLoad.call(this, id, ...args);
};
let DocsLayout: React.ComponentType<{ children: React.ReactNode }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    DocsLayout = require("./layout").default;
} finally {
    loader._load = originalLoad;
}

afterEach(cleanup);

test("the docs content area fills at least one screen above the footer", () => {
    const { container } = render(<DocsLayout><p /></DocsLayout>);
    const content = container.querySelector("main")?.parentElement;
    assert.ok(content);
    assert.ok(content.classList.contains("min-h-[calc(100svh-var(--home-navbar-height))]"), content.className);
    assert.equal(content.nextElementSibling?.tagName, "FOOTER");
});
