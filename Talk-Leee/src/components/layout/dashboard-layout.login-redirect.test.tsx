/**
 * A signed-out visitor to a signed-in page is sent to sign-in with the whole
 * address they wanted (path and query), written once-encoded with the trailing
 * slash the site uses, so /billing/plans/?interval=year still opens on the
 * yearly period after sign-in.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
let pathname = "/billing/plans/";
const replaced: string[] = [];
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => pathname, useRouter: () => ({ push() {}, replace(to: string) { replaced.push(to); } }) };
    if (id.endsWith("/sidebar") || id === "./sidebar") return { Sidebar: nothing };
    if (id.includes("notifications/notification-bell")) return { NotificationBell: nothing };
    if (id.includes("notifications/qualified-lead-alerts")) return { QualifiedLeadAlerts: nothing };
    if (id.includes("notifications/low-minutes-alerts")) return { LowMinutesAlerts: nothing };
    if (id.includes("notifications/failed-call-alerts")) return { FailedCallAlerts: nothing };
    if (id.includes("ui/health-indicator")) return { HealthIndicator: nothing };
    if (id.includes("assistant/floating-assistant")) return { FloatingAssistant: nothing };
    if (id.includes("support/support-launcher")) return { SupportLauncher: nothing };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: null, isLoading: false, refreshUser: async () => undefined, logout: async () => ({ serverConfirmed: true, identityCurrent: true }) }) };
    if (id.includes("admin/suspension-state-provider")) return { SuspensionBanner: nothing, useSuspensionState: () => ({ state: { suspended: false } }) };
    if (id.includes("lib/sidebar-client")) return {
        useSidebarState: () => ({ collapsed: false, mobileOpen: false, hydrated: true }),
        useSidebarActions: () => ({ toggleCollapsed() {}, closeMobile() {}, setCollapsed() {}, setMobileOpen() {}, toggleMobile() {} }),
    };
    return originalLoad.call(this, id, ...args);
};
let DashboardLayout: React.ComponentType<{ children: React.ReactNode; title?: string }>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    DashboardLayout = require("./dashboard-layout").DashboardLayout;
} finally {
    loader._load = originalLoad;
}

const w = window as unknown as { matchMedia?: (query: string) => unknown };
if (typeof w.matchMedia !== "function") {
    w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
}

afterEach(() => { cleanup(); replaced.length = 0; window.history.replaceState(null, "", "/"); });

function visit(path: string, search: string) {
    pathname = path;
    window.history.replaceState(null, "", `${path}${search}`);
    render(<QueryClientProvider client={new QueryClient()}><DashboardLayout title="Plans">page</DashboardLayout></QueryClientProvider>);
}

test("the sign-in redirect keeps the query of the page the visitor wanted, encoded once", () => {
    visit("/billing/plans/", "?interval=year");
    assert.deepEqual(replaced, ["/auth/login/?next=%2Fbilling%2Fplans%2F%3Finterval%3Dyear"]);
});

test("without a query the redirect carries the path only, with the trailing slash", () => {
    visit("/billing/plans/", "");
    assert.deepEqual(replaced, ["/auth/login/?next=%2Fbilling%2Fplans%2F"]);
});
