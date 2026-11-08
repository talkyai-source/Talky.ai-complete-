import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

// Real Sidebar with the same boundary stubs as sidebar.test.tsx; the signed-in
// role is switched per test. Plan 3.5: every member of a customer account
// gets the Team entry, in the desktop sidebar and in the mobile drawer.
let role = "user";
let mobileOpen = false;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/dashboard" };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("white-label/white-label-branding-provider")) return { useWhiteLabelBranding: () => null };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role }, logout: async () => {} }) };
    if (id.includes("components/ui/viewport-drawer")) return { ViewportDrawer: ({ open, children }: { open: boolean; children?: React.ReactNode }) => (open ? <div data-testid="mobile-drawer">{children}</div> : null) };
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

afterEach(() => { cleanup(); role = "user"; mobileOpen = false; });

const teamLink = (root: ParentNode) => Array.from(root.querySelectorAll("a")).find((a) => a.getAttribute("href") === "/team");

test("a plain user sees the Team entry in the desktop sidebar", () => {
    role = "user";
    const { container } = render(<Sidebar />);
    const aside = container.querySelector("aside");
    assert.ok(aside, "desktop sidebar rendered");
    const link = teamLink(aside);
    assert.equal(link !== undefined, true, "Team entry rendered");
    assert.equal(link?.textContent?.trim(), "Team");
});

test("a tenant admin and a readonly member see the same Team entry; it is not admin-only", () => {
    for (const r of ["tenant_admin", "readonly", "campaign_manager"]) {
        role = r;
        const { container, unmount } = render(<Sidebar />);
        const aside = container.querySelector("aside");
        assert.ok(aside);
        assert.equal(teamLink(aside) !== undefined, true, `${r} sees Team`);
        unmount();
    }
});

test("the mobile drawer carries the Team entry when open", () => {
    mobileOpen = true;
    const { getByTestId } = render(<Sidebar />);
    const drawer = getByTestId("mobile-drawer");
    const link = teamLink(drawer);
    assert.equal(link !== undefined, true, "Team entry in the drawer");
    assert.equal(link?.textContent?.trim(), "Team");
});
