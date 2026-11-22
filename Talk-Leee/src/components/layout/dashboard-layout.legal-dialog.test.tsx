/**
 * The signed-in shell mounts the terms re-acceptance dialog: it shows for a user
 * whose /auth/me says an acceptance is owed and for nobody else.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";

// Real DashboardLayout and real Breadcrumbs; the shell's own data-reading parts are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const pathname = "/dashboard";
let me: Record<string, unknown> = {};
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
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role: "tenant_admin", ...me }, isLoading: false, refreshUser: async () => undefined, logout: async () => ({ serverConfirmed: true, identityCurrent: true }) }) };
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

function shell() {
    return render(<QueryClientProvider client={new QueryClient()}><DashboardLayout title="Home">page</DashboardLayout></QueryClientProvider>);
}

test("the shell shows the terms dialog when an acceptance is owed", () => {
    me = { legal_acceptance_required: true, terms_version: "t1", privacy_version: "p1" };
    shell();
    assert.ok(screen.getByRole("dialog", { name: "Our terms have been updated" }));
});

test("the shell shows no dialog when nothing is owed", () => {
    me = { legal_acceptance_required: false, terms_version: "t1", privacy_version: "p1" };
    const view = shell();
    assert.equal(view.container.ownerDocument.querySelector('[role="dialog"]'), null);
});
