/**
 * FX3a2 Task 3.4 — the layout hands a page's breadcrumb names to the real
 * Breadcrumbs, so a campaign id in the path reads as the campaign's name.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";

// Real DashboardLayout and real Breadcrumbs; the shell's own data-reading parts are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
let pathname = "/campaigns/c-draft";
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => pathname, useRouter: () => ({ push() {}, replace() {} }) };
    if (id.endsWith("/sidebar") || id === "./sidebar") return { Sidebar: nothing };
    if (id.includes("notifications/notification-bell")) return { NotificationBell: nothing };
    if (id.includes("notifications/qualified-lead-alerts")) return { QualifiedLeadAlerts: nothing };
    if (id.includes("notifications/low-minutes-alerts")) return { LowMinutesAlerts: nothing };
    if (id.includes("notifications/failed-call-alerts")) return { FailedCallAlerts: nothing };
    if (id.includes("ui/health-indicator")) return { HealthIndicator: nothing };
    if (id.includes("assistant/floating-assistant")) return { FloatingAssistant: nothing };
    if (id.includes("support/support-launcher")) return { SupportLauncher: nothing };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role: "tenant_admin" }, isLoading: false }) };
    if (id.includes("admin/suspension-state-provider")) return { SuspensionBanner: nothing, useSuspensionState: () => ({ state: { suspended: false } }) };
    if (id.includes("lib/sidebar-client")) return {
        useSidebarState: () => ({ collapsed: false, mobileOpen: false, hydrated: true }),
        useSidebarActions: () => ({ toggleCollapsed() {}, closeMobile() {}, setCollapsed() {}, setMobileOpen() {}, toggleMobile() {} }),
    };
    return originalLoad.call(this, id, ...args);
};
let DashboardLayout: React.ComponentType<{ children: React.ReactNode; title?: string; breadcrumbLabels?: Record<string, string> }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    DashboardLayout = require("./dashboard-layout").DashboardLayout;
} finally {
    loader._load = originalLoad;
}

// The layout asks the window whether it is desktop-wide; the test DOM has no matchMedia.
const w = window as unknown as { matchMedia?: (query: string) => unknown };
if (typeof w.matchMedia !== "function") {
    w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
}

afterEach(() => { cleanup(); });

function crumbs(): string[] {
    const nav = screen.getByRole("navigation", { name: "Breadcrumb" });
    return within(nav).getAllByText(/\S/).map((node) => node.textContent ?? "").filter((text) => text !== "/");
}

test("FX3a2: the campaign crumb shows the campaign's name on the detail page", () => {
    pathname = "/campaigns/c-draft";
    render(<DashboardLayout title="Spring push" breadcrumbLabels={{ "c-draft": "Spring push" }}>page</DashboardLayout>);
    assert.deepEqual(crumbs(), ["Campaigns", "Spring push"]);
    assert.equal(crumbs().includes("C Draft"), false);
});

test("FX3a2: the campaign crumb shows the campaign's name on the edit page", () => {
    pathname = "/campaigns/c-draft/edit";
    render(<DashboardLayout title="Edit Campaign" breadcrumbLabels={{ "c-draft": "Spring push" }}>page</DashboardLayout>);
    assert.deepEqual(crumbs(), ["Campaigns", "Spring push", "Edit"]);
});

test("FX3a2: without a name from the page the crumb is unchanged", () => {
    pathname = "/campaigns/c-draft";
    render(<DashboardLayout title="Loading">page</DashboardLayout>);
    assert.deepEqual(crumbs(), ["Campaigns", "C Draft"]);
});
