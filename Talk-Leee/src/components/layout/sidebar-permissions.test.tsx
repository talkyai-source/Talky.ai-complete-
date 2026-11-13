import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

// QA F1-21 (QA-UI-041): every role got the same menu, so a read-only user
// could open Billing (a 403 shown as "Billing data did not load") and
// platform admins got six "not available" placeholder pages. The menu now
// follows the server's effective permissions; placeholders are not listed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let role = "tenant_admin";
let permissionsQuery: { isSuccess: boolean; data?: { permissions: string[] } } = { isSuccess: false };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/dashboard" };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("white-label/white-label-branding-provider")) return { useWhiteLabelBranding: () => null };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", email: "u@example.com", role, tenant_id: "t" }, logout: async () => {} }) };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissionsQuery };
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

afterEach(() => {
    cleanup();
    role = "tenant_admin";
    permissionsQuery = { isSuccess: false };
});

// The server's role defaults (backend/app/core/security/rbac.py ROLE_DEFAULT_PERMISSIONS).
const READONLY = ["campaigns:read", "inbound:read", "calls:read", "recordings:read", "analytics:read", "analytics:export", "tenants:read", "connectors:read"];
const AGENT = ["campaigns:read", "inbound:read", "calls:create", "calls:read", "recordings:read", "analytics:read", "tenants:read"];
const BILLING_USER = ["billing:read", "billing:update", "analytics:read", "analytics:export", "tenants:read"];
const TENANT_ADMIN = [
    "campaigns:create", "campaigns:read", "campaigns:update", "campaigns:delete", "campaigns:admin",
    "inbound:read", "inbound:manage", "inbound:assign", "inbound:controls", "users:create", "users:read", "users:update",
    "users:delete", "users:manage", "tenants:read", "tenants:update", "tenants:admin", "billing:read", "billing:update",
    "billing:admin", "calls:create", "calls:read", "calls:delete", "recordings:read", "recordings:download",
    "recordings:delete", "connectors:create", "connectors:read", "connectors:update", "connectors:delete",
    "analytics:read", "analytics:export",
];
const PLACEHOLDERS = ["Voice Security", "Abuse Detection", "Developer Hub", "API Keys", "Webhooks", "Rate Limiting", "Secrets"];

function menuFor(nextRole: string, permissions: string[] | null) {
    role = nextRole;
    permissionsQuery = permissions ? { isSuccess: true, data: { permissions } } : { isSuccess: false };
    const { container } = render(<Sidebar />);
    const nav = container.querySelector("aside nav");
    assert.ok(nav, "desktop navigation rendered");
    const bottom = Array.from(container.querySelectorAll("aside a")).filter((a) => !nav.contains(a));
    return [
        ...Array.from(nav.querySelectorAll("a, button")).map((el) => el.textContent?.trim() ?? ""),
        ...bottom.map((a) => a.textContent?.trim() ?? ""),
    ].filter((name) => name && name !== "Talk-Lee");
}

test("a read-only user sees what it may read, and no Billing", () => {
    const menu = menuFor("readonly", READONLY);
    for (const name of ["Dashboard", "Campaigns", "Inbound Campaigns", "Call History", "Contacts", "Analytics", "Recordings", "Connectors", "AI Options", "Security", "Settings"]) {
        assert.ok(menu.includes(name), `${name} missing from ${menu.join(", ")}`);
    }
    assert.equal(menu.some((name) => name.startsWith("Billing")), false, `Billing shown: ${menu.join(", ")}`);
    assert.equal(menu.includes("Audit Logs"), false);
});

test("an agent sees no Connectors and no Billing", () => {
    const menu = menuFor("agent", AGENT);
    assert.ok(menu.includes("Call History"));
    assert.ok(menu.includes("Recordings"));
    assert.equal(menu.includes("Connectors"), false);
    assert.equal(menu.some((name) => name.startsWith("Billing")), false);
});

test("a billing user sees Billing and none of the call, campaign or recording pages", () => {
    const menu = menuFor("billing_user", BILLING_USER);
    assert.ok(menu.some((name) => name.startsWith("Billing")), `no Billing in ${menu.join(", ")}`);
    for (const name of ["Campaigns", "Inbound Campaigns", "Call History", "Contacts", "Recordings", "Connectors"]) {
        assert.equal(menu.includes(name), false, `${name} shown to a billing user`);
    }
    assert.ok(menu.includes("Analytics"));
});

test("a tenant admin keeps every tenant page", () => {
    const menu = menuFor("tenant_admin", TENANT_ADMIN);
    for (const name of ["Dashboard", "Campaigns", "Inbound Campaigns", "Call History", "Contacts", "Analytics", "Recordings", "Connectors", "AI Options", "Security", "Settings"]) {
        assert.ok(menu.includes(name), `${name} missing`);
    }
    assert.ok(menu.some((name) => name.startsWith("Billing")));
});

test("a platform admin is not offered placeholder pages", () => {
    const menu = menuFor("platform_admin", ["platform:admin"]);
    for (const name of PLACEHOLDERS) assert.equal(menu.includes(name), false, `${name} offered: ${menu.join(", ")}`);
    assert.ok(menu.some((name) => name.startsWith("Billing")));
    assert.ok(menu.includes("Campaigns"), "platform:admin grants every page");
});

test("while the permissions load or cannot be read, the menu stays as it was", () => {
    const menu = menuFor("readonly", null);
    for (const name of ["Campaigns", "Call History", "Connectors", "AI Options"]) assert.ok(menu.includes(name), `${name} missing`);
    assert.ok(menu.some((name) => name.startsWith("Billing")), "the server still answers for Billing");
});
