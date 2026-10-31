import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

// Real Sidebar; only routing, theme, white-label, auth and the drawer/tooltip
// primitives are stubbed so the test exercises the brand block markup itself.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/dashboard" };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("white-label/white-label-branding-provider")) return { useWhiteLabelBranding: () => null };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role: "tenant_admin" }, logout: async () => {} }) };
    if (id.includes("components/ui/viewport-drawer")) return { ViewportDrawer: ({ open, children }: { open: boolean; children?: React.ReactNode }) => (open ? <div aria-label="Sidebar">{children}</div> : null) };
    if (id.includes("components/ui/hover-tooltip")) return { HoverTooltip: () => null, useHoverTooltip: () => ({ show() {}, hide() {}, state: null }) };
    if (id.includes("lib/sidebar-client")) return {
        useSidebarState: () => ({ collapsed: false, mobileOpen: false, hydrated: true }),
        useSidebarActions: () => ({ toggleCollapsed() {}, closeMobile() {}, setCollapsed() {}, setMobileOpen() {}, toggleMobile() {} }),
    };
    return originalLoad.call(this, id, ...args);
};
let Sidebar: React.ComponentType<{ className?: string }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Sidebar = require("./sidebar").Sidebar;
} finally {
    loader._load = originalLoad;
}
// The sidebar measures its labels on a frame; the test DOM has no rAF.
const w = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof w.requestAnimationFrame !== "function") {
    w.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
    w.cancelAnimationFrame = (id) => clearTimeout(id);
}

afterEach(() => cleanup());

test("only the brand name links to the homepage; the logo has no anchor ancestor", () => {
    const { container } = render(<Sidebar />);
    const aside = container.querySelector("aside");
    assert.ok(aside, "desktop sidebar rendered");

    const brand = Array.from(aside.querySelectorAll("a")).find((a) => a.textContent?.trim() === "Talk-Lee");
    assert.ok(brand, "the Talk-Lee text is inside an anchor");
    assert.equal(brand.getAttribute("href"), "/");
    assert.equal(brand.textContent?.trim(), "Talk-Lee");
    assert.equal(brand.querySelector("svg"), null, "the anchor wraps only the text");

    const logo = aside.querySelector("svg[viewBox='327 327 369 369']");
    assert.ok(logo, "logo rendered");
    assert.equal(logo.getAttribute("aria-hidden"), "true");
    assert.equal(logo.closest("a"), null, "the logo has no anchor ancestor");
    assert.equal(logo.parentElement?.tagName, "DIV");
    assert.equal(logo.parentElement?.getAttribute("href"), null);
    assert.equal(logo.parentElement?.getAttribute("role"), null);
    // The wrapper keeps the expanded-state collapse classes.
    assert.match(logo.parentElement?.className ?? "", /max-w-\[190px\] opacity-100 translate-x-0/);
});
