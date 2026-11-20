import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { backendApi } from "@/lib/backend-api";
import type { RbacRole, TenantMember } from "@/lib/models";
import { teamApi } from "@/lib/team-api";
import { renderWithQueryClient } from "@/test-utils/render";

let currentUser: { id: string; email: string; role: string; tenant_id: string } | null = null;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, replace() {} }), usePathname: () => "/team", useSearchParams: () => new URLSearchParams("") };
    if (id === "next/link") {
        return { __esModule: true, default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> };
    }
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div> };
    }
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: currentUser, logout: async () => {} }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

const SLOW = 20_000;
const role = (name: string, level: number): RbacRole => ({ id: `role-${name}`, name, description: null, level, is_system_role: true, tenant_scoped: true });
const ROLES = [role("tenant_admin", 60), role("user", 40), role("agent", 35)];
const member = (over: Partial<TenantMember> & Pick<TenantMember, "id" | "email">): TenantMember => ({
    name: null, role: "user", is_primary: false, status: "active", joined_at: "2026-01-05T09:30:00Z", ...over,
});
const ADMIN = member({ id: "tu-1", email: "admin@example.test", name: "Avery Admin", role: "tenant_admin", is_primary: true });
const ACTIVE_ROW = member({ id: "tu-2", email: "casey@example.test", name: "Casey" });
const SUSPENDED_ROW = member({ id: "tu-5", email: "sam@example.test", name: "Sam", role: "agent", status: "suspended" });
const row = (id: string) => within(screen.getAllByTestId("team-member-row").find((el) => el.getAttribute("data-member-id") === id)!);

beforeEach(() => {
    mock.method(teamApi, "listInvites", async () => []);
    mock.method(backendApi.team, "roles", async () => ROLES);
    currentUser = { id: "user-admin", email: "admin@example.test", role: "tenant_admin", tenant_id: "tenant-a" };
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    currentUser = null;
});

test("a suspended member has a Reactivate button that sends status active and reloads", async () => {
    const members = mock.method(backendApi.team, "members", async (status: string) => (status === "active" ? [ADMIN, ACTIVE_ROW] : status === "suspended" ? [SUSPENDED_ROW] : []));
    const update = mock.method(backendApi.team, "updateMember", async () => {});
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.ok(screen.getByTestId("team-member-list")), { timeout: SLOW });
    const readsBefore = members.mock.callCount();

    assert.equal(row("tu-2").queryByRole("button", { name: "Reactivate" }) === null, true, "an active member has no Reactivate");
    fireEvent.click(row("tu-5").getByRole("button", { name: "Reactivate" }));
    await waitFor(() => assert.equal(update.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(update.mock.calls[0]!.arguments[0], { id: "tu-5", status: "active" });
    await waitFor(() => assert.equal(members.mock.callCount() > readsBefore, true), { timeout: SLOW });
});

test("Deactivate opens the confirmation and sends nothing until it is confirmed", async () => {
    mock.method(backendApi.team, "members", async (status: string) => (status === "active" ? [ADMIN, ACTIVE_ROW] : []));
    const update = mock.method(backendApi.team, "updateMember", async () => {});
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.ok(screen.getByTestId("team-member-list")), { timeout: SLOW });

    fireEvent.click(row("tu-2").getByRole("button", { name: "Deactivate" }));
    const dialog = await screen.findByRole("dialog", {}, { timeout: SLOW });
    assert.equal(dialog.getAttribute("aria-label"), "Deactivate this team member?");
    assert.equal(within(dialog).getByText("They are signed out and cannot sign in until you reactivate them. Their work stays in the account.") !== null, true);
    assert.equal(update.mock.callCount(), 0, "opening the dialog sends nothing");

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 0, "Cancel sends nothing");
});
