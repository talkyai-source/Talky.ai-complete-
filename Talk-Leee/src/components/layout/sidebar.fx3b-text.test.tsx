/**
 * FX3b Task 3.1 — the menu row for /inbound-campaigns carries the page's own
 * title, "Inbound Campaigns". Same boundary stubs as sidebar.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    // Their side reads effective permissions (QA F1-21); unknown offers every action.
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
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

test("FX3b 3.1: the menu row for /inbound-campaigns is named 'Inbound Campaigns'", () => {
    const { container } = render(<Sidebar />);
    const rows = Array.from(container.querySelectorAll('aside a[href="/inbound-campaigns"]'));
    assert.equal(rows.length, 1);
    assert.equal(rows[0].textContent?.trim(), "Inbound Campaigns");
    const names = Array.from(container.querySelectorAll("aside a")).map((node) => node.textContent?.trim());
    assert.equal(names.includes("Inbound"), false, names.join(" | "));
    assert.equal(names.includes("Call History"), true);
});
