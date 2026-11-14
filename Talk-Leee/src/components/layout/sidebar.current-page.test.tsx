/**
 * FX4a Task 5.1 — the customer sidebar marks exactly one entry as the current
 * page: one highlight, and aria-current="page" on that same entry. On
 * /admin/audit-logs that is "Audit Logs" alone (it used to highlight
 * "Billing & Logs", "Audit Logs" and "Security Center" together, because
 * /admin matched by prefix).
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";

// Real Sidebar with the same boundary stubs as sidebar-team.test.tsx; the
// path, the collapsed state and the mobile drawer are switched per test.
let pathname = "/dashboard";
let collapsed = false;
let mobileOpen = false;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => pathname };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("white-label/white-label-branding-provider")) return { useWhiteLabelBranding: () => null };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role: "platform_admin" }, logout: async () => {} }) };
    // Unknown permissions keep every entry.
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false }) };
    if (id.includes("components/ui/viewport-drawer")) return { ViewportDrawer: ({ open, children }: { open: boolean; children?: React.ReactNode }) => (open ? <div data-testid="mobile-drawer">{children}</div> : null) };
    if (id.includes("components/ui/hover-tooltip")) return { HoverTooltip: () => null, useHoverTooltip: () => ({ show() {}, hide() {}, state: null }) };
    if (id.includes("lib/sidebar-client")) return {
        useSidebarState: () => ({ collapsed, mobileOpen, hydrated: true }),
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

afterEach(() => { cleanup(); pathname = "/dashboard"; collapsed = false; mobileOpen = false; });

const label = (element: Element) => (element.textContent ?? "").trim();
/** Entries carrying aria-current="page". */
const marked = (root: ParentNode) => Array.from(root.querySelectorAll('[aria-current="page"]')).map(label);
/** Entries drawn with the active background (the class itself, not its hover: variant). */
const highlighted = (root: ParentNode) => Array.from(root.querySelectorAll("a, button"))
    .filter((element) => Array.from(element.classList).some((name) => name === "bg-sidebar-accent" || name === "bg-sidebar-accent/70"))
    .map(label);

function desktop(path: string) {
    pathname = path;
    const { container } = render(<Sidebar />);
    const aside = container.querySelector("aside");
    assert.ok(aside, "desktop sidebar rendered");
    return aside;
}

test("/admin/audit-logs marks Audit Logs alone", () => {
    const aside = desktop("/admin/audit-logs");
    assert.deepEqual(marked(aside), ["Audit Logs"]);
    assert.deepEqual(highlighted(aside), ["Audit Logs"]);
    const names = Array.from(aside.querySelectorAll("a, button")).map(label);
    assert.equal(names.includes("Billing & Logs") && names.includes("Security Center"), true, "the other two entries are still listed");
});

test("every listed route marks exactly one entry, and that entry is the one highlighted", () => {
    const routes: Array<[string, string]> = [
        ["/dashboard", "Dashboard"], ["/campaigns", "Campaigns"], ["/inbound-campaigns", "Inbound Campaigns"], ["/calls", "Call History"],
        ["/contacts", "Contacts"], ["/analytics", "Analytics"], ["/recordings", "Recordings"], ["/connectors", "Connectors"],
        ["/ai-options", "AI Options"], ["/security", "Security"], ["/team", "Team"], ["/billing", "Billing"],
        ["/admin/audit-logs", "Audit Logs"], ["/admin", "Security Center"], ["/admin/reviews", "Agent Quality"], ["/settings", "Settings"],
        // Pages under an entry belong to it.
        ["/campaigns/abc/edit", "Campaigns"], ["/billing/invoices/1", "Billing"], ["/admin/reviews/7", "Agent Quality"], ["/calls/9", "Call History"],
    ];
    for (const [path, entry] of routes) {
        const aside = desktop(path);
        assert.deepEqual(marked(aside), [entry], `${path}: aria-current`);
        assert.deepEqual(highlighted(aside), [entry], `${path}: highlight`);
        cleanup();
    }
});

test("a route with no entry marks nothing", () => {
    const aside = desktop("/profile");
    assert.deepEqual(marked(aside), []);
    assert.deepEqual(highlighted(aside), []);
});

test("closing the group hands the mark to the group itself; opening it hands it back", () => {
    const aside = desktop("/admin/audit-logs");
    const group = Array.from(aside.querySelectorAll("button")).find((button) => label(button) === "Billing & Logs");
    assert.ok(group, "the group button");
    fireEvent.click(group);
    assert.deepEqual(marked(aside), ["Billing & Logs"]);
    assert.deepEqual(highlighted(aside), ["Billing & Logs"]);
    fireEvent.click(group);
    assert.deepEqual(marked(aside), ["Audit Logs"]);
    assert.deepEqual(highlighted(aside), ["Audit Logs"]);
});

test("the collapsed sidebar marks one entry too", () => {
    collapsed = true;
    const aside = desktop("/admin/audit-logs");
    assert.equal(aside.querySelectorAll('[aria-current="page"]').length, 1);
    assert.equal(highlighted(aside).length, 1);
    assert.equal(marked(aside)[0]?.startsWith("Billing & Logs"), true, "the group that holds Audit Logs");
});

test("the mobile menu marks the same single entry", () => {
    mobileOpen = true;
    for (const [path, entry] of [["/admin/audit-logs", "Audit Logs"], ["/dashboard", "Dashboard"], ["/campaigns", "Campaigns"]] as const) {
        pathname = path;
        const { getByTestId } = render(<Sidebar />);
        const drawer = getByTestId("mobile-drawer");
        assert.deepEqual(marked(drawer), [entry], `${path}: aria-current`);
        assert.deepEqual(highlighted(drawer), [entry], `${path}: highlight`);
        cleanup();
    }
});

// FX4a2 Task 3 — the links of a closed group are out of the Tab order; opening
// the group puts them back. The one-current-entry rule is unchanged.
test("a closed group's links cannot be reached with Tab; an open group's can", () => {
    const aside = desktop("/admin/audit-logs");
    const group = Array.from(aside.querySelectorAll("button")).find((button) => label(button) === "Billing & Logs");
    assert.ok(group, "the group button");
    const links = () => Array.from(group.parentElement!.querySelectorAll("a"));
    assert.equal(links().length > 0, true, "the group has links");
    assert.equal(links().every((link) => link.tabIndex === 0 && link.getAttribute("tabindex") === null), true, "open: every link is a Tab stop");
    fireEvent.click(group);
    assert.equal(links().every((link) => link.getAttribute("tabindex") === "-1"), true, "closed: no link is a Tab stop");
    assert.equal(group.tabIndex, 0, "the group button itself stays reachable");
    assert.deepEqual(marked(aside), ["Billing & Logs"]);
    fireEvent.click(group);
    assert.equal(links().every((link) => link.tabIndex === 0 && link.getAttribute("tabindex") === null), true, "open again: every link is a Tab stop");
    assert.deepEqual(marked(aside), ["Audit Logs"]);
    // Links outside any group are never taken out.
    const dashboard = Array.from(aside.querySelectorAll("a")).find((link) => label(link) === "Dashboard");
    assert.equal(dashboard !== undefined && dashboard.getAttribute("tabindex") === null, true);
});

// FX4b Task 0 — a closed group's links are also out of the accessibility tree
// (the group's panel is inert); opening the group puts them back.
test("a closed group's links are hidden from a screen reader; an open group's are not", () => {
    const aside = desktop("/admin/audit-logs");
    const group = Array.from(aside.querySelectorAll("button")).find((button) => label(button) === "Billing & Logs");
    assert.ok(group, "the group button");
    const links = () => Array.from(group.parentElement!.querySelectorAll("a"));
    const hiddenFromAll = (link: Element) => link.closest("[inert]") !== null;
    assert.equal(links().length > 0, true, "the group has links");
    assert.equal(links().some(hiddenFromAll), false, "open: no link sits inside an inert panel");
    fireEvent.click(group);
    assert.equal(links().every(hiddenFromAll), true, "closed: every link sits inside an inert panel");
    assert.equal(group.closest("[inert]") === null, true, "the group button itself is not hidden");
    fireEvent.click(group);
    assert.equal(links().some(hiddenFromAll), false, "open again: the links are back");
    // Every other group starts closed, and nothing outside a group is hidden.
    const inertPanels = Array.from(aside.querySelectorAll("[inert]"));
    assert.equal(inertPanels.every((panel) => panel.previousElementSibling?.tagName === "BUTTON"), true, "only group panels are inert");
    const dashboard = Array.from(aside.querySelectorAll("a")).find((link) => label(link) === "Dashboard");
    assert.equal(dashboard !== undefined && dashboard.closest("[inert]") === null, true);
});
