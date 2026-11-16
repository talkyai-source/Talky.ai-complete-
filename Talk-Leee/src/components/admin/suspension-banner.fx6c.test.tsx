/**
 * FX6c Tasks 2 and 4 (plan 3.6: "Show a clear account status banner." /
 * "If policy allows, keep Billing and Support accessible for payment or
 * appeal."). Admin "Suspend" (account_state suspended_subscription) shows the
 * banner with the server's own sentence and makes nothing inert; a hold keeps
 * its text and its inert page; the operator's free-text note is never shown.
 *
 * Real SuspensionStateProvider, real SuspensionBanner and real DashboardLayout;
 * only the shell's data-reading parts and the signed-in user are stubbed.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

type User = Record<string, unknown> | null;
let user: User = null;
let reads = 0;

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const refreshUser = async () => { reads++; };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/billing", useRouter: () => ({ push() {}, replace() {} }) };
    if (id.endsWith("/sidebar") || id === "./sidebar") return { Sidebar: nothing };
    if (id.includes("notifications/notification-bell")) return { NotificationBell: nothing };
    if (id.includes("notifications/qualified-lead-alerts")) return { QualifiedLeadAlerts: nothing };
    if (id.includes("notifications/failed-call-alerts")) return { FailedCallAlerts: nothing };
    if (id.includes("ui/health-indicator")) return { HealthIndicator: nothing };
    if (id.includes("ui/offline-banner")) return { OfflineBanner: nothing };
    if (id.includes("assistant/floating-assistant")) return { FloatingAssistant: nothing };
    if (id.includes("support/support-launcher")) return { SupportLauncher: nothing };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user, loading: false, isLoading: false, refreshUser }) };
    if (id.includes("lib/sidebar-client")) return {
        useSidebarState: () => ({ collapsed: false, mobileOpen: false, hydrated: true }),
        useSidebarActions: () => ({ toggleCollapsed() {}, closeMobile() {}, setCollapsed() {}, setMobileOpen() {}, toggleMobile() {} }),
    };
    return originalLoad.call(this, id, ...args);
};
let DashboardLayout: React.ComponentType<{ children: React.ReactNode; title?: string }>;
let SuspensionStateProvider: React.ComponentType<{ children: React.ReactNode }>;
try {
    /* eslint-disable @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above */
    SuspensionStateProvider = require("./suspension-state-provider").SuspensionStateProvider;
    DashboardLayout = require("../layout/dashboard-layout").DashboardLayout;
    /* eslint-enable @typescript-eslint/no-require-imports */
} finally {
    loader._load = originalLoad;
}

const w = window as unknown as { matchMedia?: (query: string) => unknown };
if (typeof w.matchMedia !== "function") {
    w.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
}

afterEach(() => { cleanup(); user = null; reads = 0; });

const SENTENCE = "This account is suspended, so campaigns cannot start. Billing and Support stay available.";
const HOLD_TEXT = "This tenant account is suspended. Interactive actions are disabled until access is restored.";
const ME = { id: "u1", email: "a@example.test", role: "tenant_admin", tenant_id: "t1" };

const page = () => <SuspensionStateProvider><DashboardLayout title="Billing"><button>Pay now</button></DashboardLayout></SuspensionStateProvider>;
const banner = () => screen.queryByTestId("account-status-banner");
/** The wrapper the layout makes inert for a hold. */
const content = () => screen.getByRole("button", { name: "Pay now", hidden: true }).parentElement as HTMLElement;

test("suspended_subscription: the banner shows the existing title and the server's sentence, and nothing is made inert", () => {
    user = { ...ME, account_state: "suspended_subscription", account_state_reason: "subscription_suspended" };
    render(page());
    assert.equal(banner() !== null, true, "the banner shows");
    assert.equal(banner()!.getAttribute("role"), "status");
    assert.equal(banner()!.textContent, `Account suspended${SENTENCE}`);
    assert.equal(content().hasAttribute("inert"), false, "the page is not inert");
    assert.equal(content().hasAttribute("aria-disabled"), false);
    assert.equal(content().className.includes("pointer-events-none"), false);
    assert.equal(content().parentElement!.querySelector("[aria-hidden].absolute") === null, true, "no cover is laid over the page");
    assert.equal((screen.getByRole("button", { name: "Pay now" }) as HTMLButtonElement).disabled, false);
});

test("suspended_subscription: a later profile read that says active removes the banner", () => {
    user = { ...ME, account_state: "suspended_subscription" };
    const view = render(page());
    assert.equal(banner() !== null, true);
    user = { ...ME, account_state: "active" };
    view.rerender(page());
    assert.equal(banner() === null, true);
    assert.equal(reads, 0, "no extra profile read is started by the banner");
});

test("active, or an answer without account_state: no banner", () => {
    user = { ...ME, account_state: "active" };
    render(page());
    assert.equal(banner() === null, true);
    cleanup();
    user = { ...ME };
    render(page());
    assert.equal(banner() === null, true);
});

test("hold: the banner keeps its text and the page stays inert, and the operator's note is not shown", () => {
    user = {
        ...ME, tenant_status: "suspended", suspended_scope: "tenant", suspension_reason: "Chargeback case 4411, do not restore",
        account_state: "suspended_hold", account_state_reason: "tenant_hold",
    };
    render(page());
    assert.equal(banner()!.textContent, `Account suspended${HOLD_TEXT}`);
    assert.equal(document.body.textContent?.includes("Reason:"), false, "no Reason line");
    assert.equal(document.body.textContent?.includes("Chargeback case 4411"), false, "the operator's note is not shown");
    assert.equal(content().hasAttribute("inert"), true, "the page is inert, as before");
    assert.equal(content().getAttribute("aria-disabled"), "true");
    assert.equal(content().className.includes("pointer-events-none"), true);
});

test("partner hold: the existing partner text, still no operator note", () => {
    user = { ...ME, partner_status: "suspended", suspended_scope: "partner", suspension_reason: "Partner contract ended", account_state: "suspended_hold", account_state_reason: "partner_hold" };
    render(page());
    assert.equal(banner()!.textContent, "Account suspendedThis partner account is suspended. Interactive actions are disabled until access is restored.");
    assert.equal(document.body.textContent?.includes("Partner contract ended"), false);
    assert.equal(content().hasAttribute("inert"), true);
});
