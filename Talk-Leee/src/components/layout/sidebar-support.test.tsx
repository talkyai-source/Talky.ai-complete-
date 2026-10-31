import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";
import { publishSupportStatus, resetSupportStatusForTests } from "@/lib/support-status-store";

// Real Sidebar with the same boundary stubs as sidebar.test.tsx. Support's
// visibility and unread badge come from the status store the launcher feeds.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/dashboard" };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("white-label/white-label-branding-provider")) return { useWhiteLabelBranding: () => null };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role: "user" }, logout: async () => {} }) };
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
const w = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof w.requestAnimationFrame !== "function") {
    w.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
    w.cancelAnimationFrame = (id) => clearTimeout(id);
}

afterEach(() => { cleanup(); resetSupportStatusForTests(); });

const supportLink = (root: HTMLElement) => Array.from(root.querySelectorAll("aside a")).find((a) => a.getAttribute("href") === "/support");

test("without a published support status there is no Support entry", () => {
    const { container } = render(<Sidebar />);
    assert.equal(supportLink(container), undefined);
});

test("a visible support status adds the entry; the unread count is the badge", () => {
    const { container } = render(<Sidebar />);
    act(() => publishSupportStatus({ visible: true, unreadCount: 0 }));
    const link = supportLink(container);
    assert.ok(link, "Support entry rendered");
    assert.equal(link.textContent?.trim(), "Support");
    assert.equal(link.querySelector("[data-support-unread-badge]"), null, "no badge at zero");
    act(() => publishSupportStatus({ visible: true, unreadCount: 3 }));
    const badge = supportLink(container)?.querySelector("[data-support-unread-badge]");
    assert.ok(badge, "badge rendered");
    assert.equal(badge.textContent, "3");
    assert.equal(badge.getAttribute("aria-label"), "3 unread");
    act(() => publishSupportStatus({ visible: true, unreadCount: 120 }));
    assert.equal(supportLink(container)?.querySelector("[data-support-unread-badge]")?.textContent, "99+");
    act(() => publishSupportStatus({ visible: false, unreadCount: 0 }));
    assert.equal(supportLink(container), undefined, "entry removed when support goes away");
});
