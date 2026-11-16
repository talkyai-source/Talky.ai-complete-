/**
 * FX6d Task 0.2 (P6) — while the side menu is open, the menu and its backdrop
 * sit in a layer above the floating Help (z-40) and Assistant (z-50) buttons.
 * Only the side menu's drawer is raised: every other ViewportDrawer keeps the
 * shared z-50 layer.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { ViewportDrawer } from "@/components/ui/viewport-drawer";

// Real Sidebar and real ViewportDrawer; the same boundary stubs as
// sidebar.current-page.test.tsx without the drawer stub.
let mobileOpen = false;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/dashboard" };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("white-label/white-label-branding-provider")) return { useWhiteLabelBranding: () => null };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role: "platform_admin" }, logout: async () => {} }) };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false }) };
    if (id.includes("components/ui/hover-tooltip")) return { HoverTooltip: () => null, useHoverTooltip: () => ({ show() {}, hide() {}, state: null }) };
    if (id.includes("lib/sidebar-client")) return {
        useSidebarState: () => ({ collapsed: false, mobileOpen, hydrated: true }),
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
const w = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof w.requestAnimationFrame !== "function") {
    w.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
    w.cancelAnimationFrame = (id) => clearTimeout(id);
}

afterEach(() => { cleanup(); mobileOpen = false; });

/** The z-index a Tailwind layer class gives ("z-50" or "z-[60]"), or null. */
function layerOf(element: Element | null | undefined): number | null {
    for (const name of Array.from(element?.classList ?? [])) {
        const match = /^z-\[?(\d+)\]?$/.exec(name);
        if (match) return Number(match[1]);
    }
    return null;
}

/** The fixed full-window root that holds a drawer's backdrop and panel. */
function drawerRoot(label: string): Element | null {
    return document.querySelector(`[role="dialog"][aria-label="${label}"]`)?.parentElement ?? null;
}

const HELP_BUTTON_LAYER = 40; // components/support/help-button.tsx:23
const ASSISTANT_BUTTON_LAYER = 50; // components/assistant/floating-assistant.tsx:729

test("the open side menu and its backdrop sit above the floating Help and Assistant buttons", () => {
    mobileOpen = true;
    render(<Sidebar />);
    const root = drawerRoot("Sidebar");
    assert.equal(root !== null, true, "side menu drawer open");
    const layer = layerOf(root);
    assert.equal(layer !== null && layer > ASSISTANT_BUTTON_LAYER && layer > HELP_BUTTON_LAYER, true, `drawer layer ${layer} is above 50`);
    // One layer class only: the shared z-50 is replaced, not kept beside it.
    assert.equal(Array.from(root?.classList ?? []).filter((name) => name.startsWith("z-")).length, 1);
    // The backdrop and the panel are both inside that raised root.
    assert.equal(root?.querySelector('button[aria-label="Close drawer"]') !== null, true);
    assert.equal(root?.querySelector('[role="dialog"]') !== null, true);
});

test("a closed side menu draws no drawer layer at all", () => {
    mobileOpen = false;
    render(<Sidebar />);
    assert.equal(drawerRoot("Sidebar") === null, true);
});

test("any other drawer keeps the shared z-50 layer", () => {
    render(<ViewportDrawer open onOpenChange={() => {}} ariaLabel="Other drawer"><p>content</p></ViewportDrawer>);
    assert.equal(layerOf(drawerRoot("Other drawer")), 50);
});
