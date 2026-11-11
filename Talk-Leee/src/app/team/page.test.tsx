import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { backendApi } from "@/lib/backend-api";
import { ApiClientError } from "@/lib/http-client";
import type { RbacRole, TenantMember } from "@/lib/models";
import { teamApi, type TeamInvite } from "@/lib/team-api";
import { renderWithQueryClient } from "@/test-utils/render";

// The one Team page (/team): the member list and role controls for everyone,
// and for admins the invite form and the invites still waiting (those used to
// be a second page at Settings -> Team, which now sends people here).
// Real page, real Select, real ConfirmDialog/Modal and the real team hooks.
// Only layout, routing, theme and the signed-in user are stubbed.
let currentUser: { id: string; email: string; role: string; tenant_id: string } | null = null;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, replace() {} }), usePathname: () => "/team", useSearchParams: () => new URLSearchParams("") };
    if (id === "next/link") {
        return {
            __esModule: true,
            default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
        };
    }
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ title, children }: { title?: string; children: React.ReactNode }) => <div data-layout-title={title}>{children}</div> };
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

// jsdom has no layout engine; the Select panel scrolls its active option into view.
if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
}

// A failing assert.equal(<DOM node>, null) makes node:assert print the whole
// JSDOM node and never return; compare booleans instead.
const absent = (node: unknown, message?: string) => assert.equal(node === null, true, message ?? "expected nothing to be rendered");
const present = (node: unknown, message?: string) => assert.equal(node !== null, true, message ?? "expected something to be rendered");

const SLOW = 20_000;
const INVITE_TEXT = "Inviting teammates isn't available yet.";
const PLATFORM_ONLY_TEXT = ["platform:admin", "platform:tenants:manage", "platform:users:manage", "platform:settings:manage", "platform_admin", "partner_admin", "Platform Admin", "Partner Admin", "Platform admin", "Partner admin"];
const TENANT_ROLE_NAMES: Record<string, string> = { tenant_admin: "Tenant admin", campaign_manager: "Campaign manager", user: "User", agent: "Agent", billing_user: "Billing user", readonly: "Readonly" };

const TENANT_ADMIN = { id: "user-admin", email: "admin@example.test", role: "tenant_admin", tenant_id: "tenant-a" };
const PLAIN_USER = { id: "user-plain", email: "plain@example.test", role: "user", tenant_id: "tenant-a" };

const role = (name: string, level: number, tenant_scoped: boolean): RbacRole => ({
    id: `role-${name}`, name, description: null, level, is_system_role: true, tenant_scoped,
});
const ROLES: RbacRole[] = [
    role("platform_admin", 100, false),
    role("partner_admin", 80, false),
    role("tenant_admin", 60, true),
    role("campaign_manager", 50, true),
    role("user", 40, true),
    role("agent", 35, true),
    role("billing_user", 30, true),
    role("readonly", 20, true),
];

const member = (over: Partial<TenantMember> & Pick<TenantMember, "id" | "email">): TenantMember => ({
    name: null, role: "user", is_primary: false, status: "active", joined_at: "2026-01-05T09:30:00Z", ...over,
});
const ADMIN_ROW = member({ id: "tu-1", email: "admin@example.test", name: "Avery Admin", role: "tenant_admin", is_primary: true });
const MANAGER_ROW = member({ id: "tu-2", email: "casey@example.test", name: "Casey Campaigns", role: "campaign_manager" });
const USER_ROW = member({ id: "tu-3", email: "plain@example.test", name: null, role: "user" });
const ACTIVE = [ADMIN_ROW, MANAGER_ROW, USER_ROW];
const PENDING = [member({ id: "tu-4", email: "pending@example.test", name: "Pat Pending", role: "readonly", status: "pending" })];
const SUSPENDED = [member({ id: "tu-5", email: "sam@example.test", name: "Sam Suspended", role: "agent", status: "suspended" })];
// A second administrator: the signed-in admin keeps this row's controls, never their own.
const OLIVE_ROW = member({ id: "tu-6", email: "olive@example.test", name: "Olive Admin", role: "tenant_admin" });

type Lists = { active: TenantMember[]; pending: TenantMember[]; suspended: TenantMember[] };
function mockMembers(lists: Lists = { active: ACTIVE, pending: PENDING, suspended: SUSPENDED }) {
    return mock.method(backendApi.team, "members", async (status: string) => lists[status as keyof Lists] ?? []);
}
function mockRoles(roles: RbacRole[] = ROLES) {
    return mock.method(backendApi.team, "roles", async () => roles);
}

// The page also asks an admin for the invites still waiting, so every test starts
// with an answer ready (none waiting); a test that needs some sets `invitesImpl`.
let invitesReads = 0;
let invitesImpl: () => Promise<TeamInvite[]> = async () => [];
function mockInvites() {
    return mock.method(teamApi, "listInvites", async () => { invitesReads += 1; return invitesImpl(); });
}
beforeEach(() => {
    invitesReads = 0;
    invitesImpl = async () => [];
    mockInvites();
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    currentUser = null;
});

const rows = () => screen.getAllByTestId("team-member-row");
const row = (id: string) => within(screen.getAllByTestId("team-member-row").find((el) => el.getAttribute("data-member-id") === id)!);
const waitForList = () => waitFor(() => assert.ok(screen.getByTestId("team-member-list")), { timeout: SLOW });

async function openRoleMenu(email: string) {
    const trigger = screen.getByRole("combobox", { name: `Role: ${email}` });
    fireEvent.click(trigger);
    await waitFor(() => assert.ok(screen.getByRole("listbox")), { timeout: SLOW });
    return screen.getAllByRole("option").map((o) => o.textContent ?? "");
}

test("every member sees the list from GET /rbac/tenant-users with name, email, role and status; a plain user gets one active-only read and no controls", async () => {
    currentUser = PLAIN_USER;
    const members = mockMembers();
    const roles = mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();

    assert.equal(members.mock.callCount(), 1);
    assert.equal(members.mock.calls[0]!.arguments[0], "active");
    assert.equal(roles.mock.callCount(), 0, "a plain user never fetches the roles");
    assert.equal(rows().length, 3);

    assert.equal(row("tu-1").getByTestId("team-member-name").textContent, "Avery Admin");
    assert.equal(row("tu-1").getByTestId("team-member-email").textContent, "admin@example.test");
    assert.equal(row("tu-1").getByTestId("team-member-role").textContent, "Tenant admin");
    assert.equal(row("tu-1").getByTestId("team-member-status").textContent, "Active");
    assert.equal(row("tu-2").getByTestId("team-member-role").textContent, "Campaign manager");
    // No name: the email stands in.
    assert.equal(row("tu-3").getByTestId("team-member-name").textContent, "plain@example.test");

    assert.equal(screen.queryAllByRole("combobox").length, 0, "no role control for a user the backend does not allow");
    assert.equal(screen.queryAllByRole("button", { name: "Deactivate" }).length, 0);
    assert.equal(screen.queryAllByRole("button", { name: "Remove" }).length, 0);
    // Invites are for admins: a plain user is offered no Invite button and no form,
    // and the invites are never read. (This used to expect a disabled button; the
    // control is admin-only now, which is what the page actually does.)
    absent(screen.queryByTestId("team-invite-button"), "a plain user is offered no invite");
    absent(screen.queryByRole("heading", { name: "Invite someone" }));
    assert.equal(invitesReads, 0, "a plain user never reads the invites");
});

test("a tenant admin reads active, pending and suspended members and sees every status", async () => {
    currentUser = TENANT_ADMIN;
    const members = mockMembers();
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();

    assert.deepEqual(members.mock.calls.map((c) => c.arguments[0]).sort(), ["active", "pending", "suspended"]);
    assert.equal(rows().length, 5);
    assert.equal(row("tu-4").getByTestId("team-member-status").textContent, "Pending");
    assert.equal(row("tu-5").getByTestId("team-member-status").textContent, "Suspended");
    // Already suspended: Deactivate has nothing to do.
    assert.equal(row("tu-5").getByRole("button", { name: "Deactivate" }).hasAttribute("disabled"), true);
    assert.equal(row("tu-5").getByRole("button", { name: "Remove" }).hasAttribute("disabled"), false);
});

test("a failed read shows the server's message in the error state and never a list", async () => {
    currentUser = TENANT_ADMIN;
    mock.method(backendApi.team, "members", async () => { throw new Error("Access denied to this tenant"); });
    mockRoles();
    renderWithQueryClient(<Page />);

    await waitFor(() => assert.ok(screen.getByRole("alert")), { timeout: SLOW });
    assert.equal(screen.getByRole("alert").textContent, "Access denied to this tenant");
    absent(screen.queryByTestId("team-member-list"));
    assert.equal(screen.queryAllByTestId("team-member-row").length, 0);
    present(screen.queryByRole("button", { name: "Retry" }), "the existing error state offers Retry");
});

test("Invite teammate stays on this page: it scrolls to the invite form and puts the cursor in its Email field", async () => {
    // Inviting by email exists now (QA F1-15, D4-2), so the old disabled button
    // and its "isn't available yet" sentence would be untrue, and the form is on
    // this page, so the button does not link away to Settings any more.
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    const scroll = mock.method(Element.prototype, "scrollIntoView", () => {});
    renderWithQueryClient(<Page />);
    await waitForList();
    absent(screen.queryByRole("link", { name: "Invite teammate" }), "the button no longer links away");
    const email = await screen.findByLabelText("Email", undefined, { timeout: SLOW });
    assert.equal(document.activeElement === email, false, "the cursor starts elsewhere");
    const scrollsBefore = scroll.mock.callCount();

    fireEvent.click(screen.getByRole("button", { name: "Invite teammate" }));
    assert.equal(document.activeElement === email, true, "the cursor is in the Email field");
    assert.equal(scroll.mock.calls.slice(scrollsBefore).some((call) => call.this === email), true, "the form is scrolled into view");
    absent(screen.queryByTestId("team-invite-unavailable"));
    absent(screen.queryByText(INVITE_TEXT));
});

test("role options come from GET /rbac/roles: tenant-scoped roles at or below the admin's level, never platform roles", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    const roles = mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });
    assert.equal(roles.mock.callCount(), 1);

    const labels = await openRoleMenu("casey@example.test");
    assert.deepEqual(labels, ["Tenant admin", "Campaign manager", "User", "Agent", "Billing user", "Readonly"]);
    for (const text of PLATFORM_ONLY_TEXT) {
        assert.equal(document.body.textContent?.includes(text), false, `${text} is not on the page`);
    }
});

test("a member above the admin's tier shows a plain role label, not a control", async () => {
    currentUser = TENANT_ADMIN;
    // The wait below needs a row that does get a control (the admin's own row never does).
    mockMembers({ active: [MANAGER_ROW, member({ id: "tu-9", email: "partner@example.test", name: "Parker Partner", role: "partner_admin" })], pending: [], suspended: [] });
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });

    // The backend refuses any change to a member above the caller's tier
    // (tenant_users.py:55-57), and the control cannot show a value it does not
    // offer; the row keeps the label only.
    assert.equal(row("tu-9").queryAllByRole("combobox").length, 0);
    assert.equal(row("tu-9").getByTestId("team-member-role").textContent, "Partner admin");
});

test("changing a role asks first, then sends PATCH with role_name and reloads the list; a refusal shows the server's message on that row and the old role stays", async () => {
    currentUser = TENANT_ADMIN;
    const members = mockMembers();
    mockRoles();
    let fail = false;
    const update = mock.method(backendApi.team, "updateMember", async () => {
        if (fail) throw new Error("Cannot grant a role above your own tier");
    });
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });
    const readsBefore = members.mock.callCount();

    await openRoleMenu("casey@example.test");
    fireEvent.click(screen.getByRole("option", { name: "User" }));
    const first = await screen.findByRole("dialog", {}, { timeout: SLOW });
    assert.equal(update.mock.callCount(), 0, "choosing a role sends nothing until it is confirmed");
    fireEvent.click(within(first).getByRole("button", { name: "Change role" }));
    await waitFor(() => assert.equal(update.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(update.mock.calls[0]!.arguments[0], { id: "tu-2", role_name: "user" });
    await waitFor(() => assert.equal(members.mock.callCount() > readsBefore, true, "the list is reloaded from the backend"), { timeout: SLOW });
    await waitFor(() => absent(screen.queryByRole("dialog")), { timeout: SLOW });
    absent(row("tu-2").queryByTestId("team-member-error"));

    fail = true;
    await openRoleMenu("casey@example.test");
    fireEvent.click(screen.getByRole("option", { name: "Tenant admin" }));
    const second = await screen.findByRole("dialog", {}, { timeout: SLOW });
    fireEvent.click(within(second).getByRole("button", { name: "Change role" }));
    await waitFor(() => assert.ok(row("tu-2").getByTestId("team-member-error")), { timeout: SLOW });
    assert.equal(row("tu-2").getByTestId("team-member-error").textContent, "Cannot grant a role above your own tier");
    assert.equal(update.mock.callCount(), 2);
    // The backend still says campaign_manager (the mock list never changed), so the control shows it.
    assert.equal(screen.getByRole("combobox", { name: "Role: casey@example.test" }).textContent, "Campaign manager");
    assert.equal(rows().length, 5, "nothing in the list changed");
});

test("Deactivate sends PATCH with status suspended and reloads the list", async () => {
    currentUser = TENANT_ADMIN;
    const members = mockMembers();
    mockRoles();
    const update = mock.method(backendApi.team, "updateMember", async () => {});
    renderWithQueryClient(<Page />);
    await waitForList();
    const readsBefore = members.mock.callCount();

    fireEvent.click(row("tu-2").getByRole("button", { name: "Deactivate" }));
    await waitFor(() => assert.equal(update.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(update.mock.calls[0]!.arguments[0], { id: "tu-2", status: "suspended" });
    await waitFor(() => assert.equal(members.mock.callCount() > readsBefore, true), { timeout: SLOW });
});

test("Remove asks first, naming the member by name and email; Cancel sends nothing; Confirm sends one DELETE and reloads", async () => {
    currentUser = TENANT_ADMIN;
    const members = mockMembers();
    mockRoles();
    const remove = mock.method(backendApi.team, "removeMember", async () => {});
    renderWithQueryClient(<Page />);
    await waitForList();
    const readsBefore = members.mock.callCount();

    fireEvent.click(row("tu-2").getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog", {}, { timeout: SLOW });
    assert.equal(dialog.getAttribute("aria-label"), "Remove Casey Campaigns?");
    assert.equal(within(dialog).getByText("Casey Campaigns (casey@example.test)") !== null, true);
    assert.equal(within(dialog).getByText("Casey Campaigns will lose access to this workspace.") !== null, true);
    assert.equal(remove.mock.callCount(), 0, "nothing is sent by opening the confirmation");

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => absent(screen.queryByRole("dialog")), { timeout: SLOW });
    assert.equal(remove.mock.callCount(), 0, "Cancel sends nothing");
    assert.equal(members.mock.callCount(), readsBefore, "Cancel reloads nothing");

    fireEvent.click(row("tu-2").getByRole("button", { name: "Remove" }));
    const again = await screen.findByRole("dialog", {}, { timeout: SLOW });
    const confirm = within(again).getByRole("button", { name: "Remove" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => assert.equal(remove.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(remove.mock.calls[0]!.arguments[0], { id: "tu-2" });
    await waitFor(() => absent(screen.queryByRole("dialog")), { timeout: SLOW });
    await waitFor(() => assert.equal(members.mock.callCount() > readsBefore, true), { timeout: SLOW });
    assert.equal(remove.mock.callCount(), 1, "exactly one DELETE");
});

test("a refused remove shows the server's message and keeps the list", async () => {
    currentUser = TENANT_ADMIN;
    const members = mockMembers();
    mockRoles();
    const remove = mock.method(backendApi.team, "removeMember", async () => { throw new Error("Cannot remove yourself as primary tenant member"); });
    renderWithQueryClient(<Page />);
    await waitForList();
    const readsBefore = members.mock.callCount();

    // Any refusal text will do, it is the server's own words that must reach the
    // screen. The signed-in admin's own row (tu-1) has no Remove, so another member's is used.
    fireEvent.click(row("tu-2").getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog", {}, { timeout: SLOW });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => assert.equal(remove.mock.callCount(), 1), { timeout: SLOW });
    await waitFor(() => assert.ok(row("tu-2").getByTestId("team-member-error")), { timeout: SLOW });
    assert.equal(row("tu-2").getByTestId("team-member-error").textContent, "Cannot remove yourself as primary tenant member");
    // The dialog shows the same message and stays open.
    assert.equal(within(screen.getByRole("dialog")).getAllByRole("alert").some((el) => el.textContent === "Cannot remove yourself as primary tenant member"), true);
    assert.equal(rows().length, 5, "nothing in the list changed");
    assert.equal(rows().map((el) => el.getAttribute("data-member-id")).includes("tu-2"), true);
    assert.equal(members.mock.callCount(), readsBefore, "a refusal does not reload");
});

test("while a request runs, that row's controls are disabled", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    let finish: () => void = () => {};
    mock.method(backendApi.team, "updateMember", () => new Promise<void>((resolve) => { finish = resolve; }));
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });

    fireEvent.click(row("tu-2").getByRole("button", { name: "Deactivate" }));
    await waitFor(() => assert.equal(row("tu-2").getByRole("button", { name: "Deactivate" }).hasAttribute("disabled"), true), { timeout: SLOW });
    assert.equal(row("tu-2").getByRole("button", { name: "Remove" }).hasAttribute("disabled"), true);
    assert.equal(screen.getByRole("combobox", { name: "Role: casey@example.test" }).hasAttribute("disabled"), true);
    // Other rows are untouched.
    assert.equal(row("tu-3").getByRole("button", { name: "Deactivate" }).hasAttribute("disabled"), false);

    await act(async () => { finish(); });
    await waitFor(() => assert.equal(row("tu-2").getByRole("button", { name: "Deactivate" }).hasAttribute("disabled"), false), { timeout: SLOW });
});

test("no platform-only permission or role text appears anywhere on the page", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });
    await openRoleMenu("casey@example.test");
    const text = document.body.textContent ?? "";
    for (const needle of PLATFORM_ONLY_TEXT) {
        assert.equal(text.includes(needle), false, `${needle} is not on the page`);
    }
});

test("each of the six tenant roles shows its own name in the rows and in the role menu", async () => {
    const six = Object.keys(TENANT_ROLE_NAMES).map((name, i) => member({ id: `six-${i}`, email: `${name}@example.test`, name: null, role: name }));

    // Rows as a plain user: the label only.
    currentUser = PLAIN_USER;
    mockMembers({ active: six, pending: [], suspended: [] });
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();
    for (const [i, name] of Object.keys(TENANT_ROLE_NAMES).entries()) {
        assert.equal(row(`six-${i}`).getByTestId("team-member-role").textContent, TENANT_ROLE_NAMES[name], `${name} row label`);
    }
    cleanup();
    mock.restoreAll();
    mockInvites();

    // Rows and menu as a tenant admin: the control shows the member's own role, the menu lists all six names.
    currentUser = TENANT_ADMIN;
    mockMembers({ active: six, pending: [], suspended: [] });
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: readonly@example.test" })), { timeout: SLOW });
    for (const name of Object.keys(TENANT_ROLE_NAMES)) {
        assert.equal(screen.getByRole("combobox", { name: `Role: ${name}@example.test` }).textContent, TENANT_ROLE_NAMES[name], `${name} control label`);
    }
    const labels = await openRoleMenu("agent@example.test");
    assert.deepEqual(labels, Object.values(TENANT_ROLE_NAMES));
    for (const wrong of ["Tenant Admin", "Read Only", "Campaign_manager", "Billing_user"]) {
        assert.equal(document.body.textContent?.includes(wrong), false, `${wrong} is not shown`);
    }
});

test("an empty list shows the existing empty state with the existing sentence and no invented text", async () => {
    currentUser = PLAIN_USER;
    mockMembers({ active: [], pending: [], suspended: [] });
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.ok(screen.getByText("Try refreshing the page.")), { timeout: SLOW });
    absent(screen.queryByTestId("team-member-list"));
    assert.equal(document.body.textContent?.includes("No team members yet"), false);
    absent(screen.queryByRole("alert"), "an empty list is not an error");
});

// Own row and asking first: these came from the retired Settings -> Team page
// (QA F1-15, QA-UI-151); they now hold for the one Team page.

test("a person never gets controls on their own row (found by email, case ignored); another admin's row keeps them", async () => {
    currentUser = { ...TENANT_ADMIN, email: "Admin@Example.test" };
    mockMembers({ active: [...ACTIVE, OLIVE_ROW], pending: [], suspended: [] });
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });

    // Own row (admin@example.test): a label and a You marker, nothing to press or choose.
    assert.equal(row("tu-1").getByText("You") !== null, true, "own row is marked");
    assert.equal(row("tu-1").getByTestId("team-member-role").textContent, "Tenant admin");
    assert.equal(row("tu-1").queryAllByRole("combobox").length, 0, "own role change is offered");
    assert.equal(row("tu-1").queryAllByRole("button").length, 0, "own deactivation or removal is offered");
    assert.equal(screen.queryAllByRole("combobox", { name: "Role: admin@example.test" }).length, 0);
    // Everyone else keeps their controls, another admin included, and is not marked.
    for (const id of ["tu-2", "tu-6"]) {
        assert.equal(row(id).queryAllByText("You").length, 0, `${id} is not marked as you`);
        assert.equal(row(id).queryAllByRole("combobox").length, 1, `${id} keeps the role control`);
        assert.equal(row(id).getByRole("button", { name: "Deactivate" }) !== null, true, `${id} keeps Deactivate`);
        assert.equal(row(id).getByRole("button", { name: "Remove" }) !== null, true, `${id} keeps Remove`);
    }
});

test("everyone sees which row is theirs; a home-workspace flag is never shown as ownership", async () => {
    currentUser = { ...PLAIN_USER, role: "readonly" };
    mockMembers();
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();

    // ADMIN_ROW carries is_primary (a member's home workspace, not ownership).
    assert.equal(ADMIN_ROW.is_primary, true);
    assert.equal(screen.getAllByText("You").length, 1);
    assert.equal(row("tu-3").getByText("You") !== null, true, "the row with the signed-in email is marked");
    assert.equal(screen.queryAllByText("Owner").length, 0, "a home-workspace flag is shown as ownership");
    assert.equal(screen.queryAllByRole("combobox").length, 0, "a read-only role is offered role changes");
    assert.equal(screen.queryAllByRole("button", { name: /^(Remove|Deactivate)/ }).length, 0, "a read-only role is offered removal");
});

test("choosing a role asks first, naming the person and the new role; Cancel sends nothing; Confirm sends one PATCH and the list is read again", async () => {
    currentUser = TENANT_ADMIN;
    const members = mockMembers();
    mockRoles();
    const update = mock.method(backendApi.team, "updateMember", async () => {});
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });
    const readsBefore = members.mock.callCount();

    await openRoleMenu("casey@example.test");
    fireEvent.click(screen.getByRole("option", { name: "Readonly" }));
    const dialog = await screen.findByRole("dialog", {}, { timeout: SLOW });
    assert.equal(within(dialog).getByText("Casey Campaigns will become Readonly.") !== null, true);
    assert.equal(update.mock.callCount(), 0, "nothing is sent by opening the question");

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => absent(screen.queryByRole("dialog")), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 0, "Cancel sends nothing");
    assert.equal(members.mock.callCount(), readsBefore, "Cancel reloads nothing");
    assert.equal(screen.getByRole("combobox", { name: "Role: casey@example.test" }).textContent, "Campaign manager", "the role is unchanged");

    await openRoleMenu("casey@example.test");
    fireEvent.click(screen.getByRole("option", { name: "Readonly" }));
    const again = await screen.findByRole("dialog", {}, { timeout: SLOW });
    const confirm = within(again).getByRole("button", { name: "Change role" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => assert.equal(update.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(update.mock.calls[0]!.arguments[0], { id: "tu-2", role_name: "readonly" });
    await waitFor(() => assert.equal(members.mock.callCount() > readsBefore, true, "the list is read again"), { timeout: SLOW });
    await waitFor(() => absent(screen.queryByRole("dialog")), { timeout: SLOW });
    assert.equal(update.mock.callCount(), 1, "exactly one PATCH");
});

test("a role change the server refuses stays open and says why", async () => {
    currentUser = TENANT_ADMIN;
    const members = mockMembers({ active: [...ACTIVE, OLIVE_ROW], pending: [], suspended: [] });
    mockRoles();
    const refusal = "This is the workspace's last active administrator. Make someone else an administrator first.";
    mock.method(backendApi.team, "updateMember", async () => {
        throw new ApiClientError({ status: 409, code: "http_error", message: refusal, url: "http://example.test/rbac/tenant-users/tu-6", method: "PATCH" });
    });
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: olive@example.test" })), { timeout: SLOW });
    const readsBefore = members.mock.callCount();

    await openRoleMenu("olive@example.test");
    fireEvent.click(screen.getByRole("option", { name: "Readonly" }));
    const dialog = within(await screen.findByRole("dialog", {}, { timeout: SLOW }));
    await act(async () => { fireEvent.click(dialog.getByRole("button", { name: "Change role" })); });
    await waitFor(() => assert.equal(dialog.queryAllByText(refusal).length, 1), { timeout: SLOW });
    assert.equal(screen.queryAllByRole("dialog").length, 1, "the dialog closed on a refusal");
    assert.equal(row("tu-6").getByTestId("team-member-error").textContent, refusal);
    assert.equal(members.mock.callCount(), readsBefore, "the list was re-read as if the change happened");
});

// Inviting (QA F1-15, D4-2). Owner decisions, 11 Nov 2026: admins invite by
// email; the link works for 7 days and once; an existing account is refused
// by the server. Also from the retired Settings -> Team page.
const WAITING: TeamInvite = { id: "inv-1", email: "new@example.com", role: "agent", created_at: "2026-11-11T09:00:00Z", expires_at: "2026-11-18T09:00:00Z" };

async function fillInvite(email: string, roleLabel: string) {
    fireEvent.change(await screen.findByLabelText("Email", undefined, { timeout: SLOW }), { target: { value: email } });
    fireEvent.click(screen.getByRole("combobox", { name: "Role for the new person" }));
    fireEvent.click(await screen.findByRole("option", { name: roleLabel }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send invite" })); });
}

test("an admin invites someone by email with a role", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    const invite = mock.method(teamApi, "invite", async () => ({ ...WAITING, email_sent: true }));
    renderWithQueryClient(<Page />);
    await fillInvite("  New@Example.com ", "Agent");
    await waitFor(() => assert.equal(invite.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(invite.mock.calls[0]!.arguments, ["new@example.com", "agent"]);
    await waitFor(() => assert.equal(screen.getAllByText("Invite sent to new@example.com.").length, 1), { timeout: SLOW });
    await waitFor(() => assert.equal(invitesReads >= 2, true, "the waiting list is read again"), { timeout: SLOW });
    assert.equal(screen.queryAllByText(/not available yet/).length, 0);
});

test("an email that did not go out says so, and Resend sends a new link", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    invitesImpl = async () => [WAITING];
    const invite = mock.method(teamApi, "invite", async () => ({ ...WAITING, email_sent: false }));
    renderWithQueryClient(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Resend the invite to new@example.com" }, { timeout: SLOW }));
    await waitFor(() => assert.equal(invite.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(invite.mock.calls[0]!.arguments, ["new@example.com", "agent"]);
    await waitFor(() => assert.equal(screen.getAllByText(/was saved, but the email did not go out/).length, 1), { timeout: SLOW });
});

test("a refused invite says why", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    const refusal = "This email already has an account, so it cannot be invited.";
    mock.method(teamApi, "invite", async () => {
        throw new ApiClientError({ status: 409, code: "http_error", message: refusal, url: "http://example.test/rbac/tenant-invites", method: "POST" });
    });
    renderWithQueryClient(<Page />);
    await fillInvite("sam@example.com", "Read only");
    await waitFor(() => assert.equal(screen.queryAllByRole("alert").some((node) => node.textContent === refusal), true), { timeout: SLOW });
});

test("a mistyped email is caught before anything is sent", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    const invite = mock.method(teamApi, "invite", async () => ({ ...WAITING, email_sent: true }));
    renderWithQueryClient(<Page />);
    await fillInvite("not-an-email", "Agent");
    assert.equal(screen.queryAllByRole("alert").some((node) => node.textContent === "Enter a valid email address."), true);
    assert.equal(invite.mock.callCount(), 0);
});

test("withdrawing an invite asks first", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    invitesImpl = async () => [WAITING];
    const withdraw = mock.method(teamApi, "withdrawInvite", async () => undefined);
    renderWithQueryClient(<Page />);
    const ask = async () => {
        fireEvent.click(await screen.findByRole("button", { name: "Withdraw the invite to new@example.com" }, { timeout: SLOW }));
        return within(await screen.findByRole("dialog", {}, { timeout: SLOW }));
    };

    let dialog = await ask();
    assert.ok(dialog.getByText("The invite link sent to new@example.com will stop working."));
    assert.equal(withdraw.mock.callCount(), 0, "opening the question sends nothing");
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => absent(screen.queryByRole("dialog")), { timeout: SLOW });
    assert.equal(withdraw.mock.callCount(), 0, "Cancel sends nothing");

    dialog = await ask();
    await act(async () => { fireEvent.click(dialog.getByRole("button", { name: "Withdraw" })); });
    await waitFor(() => assert.equal(withdraw.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(withdraw.mock.calls[0]!.arguments, ["inv-1"]);
    await waitFor(() => assert.equal(invitesReads >= 2, true, "the waiting list is read again"), { timeout: SLOW });
});

test("someone who is not an admin sees no invite form and never asks for invites", async () => {
    currentUser = { ...PLAIN_USER, role: "agent" };
    mockMembers();
    mockRoles();
    renderWithQueryClient(<Page />);
    await waitForList();
    assert.equal(screen.queryAllByRole("heading", { name: "Invite someone" }).length, 0);
    absent(screen.queryByTestId("team-invite-button"));
    assert.equal(invitesReads, 0);
});

test("a failed read of the waiting list says so with a request reference and a way to try again; the member list is unaffected", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    invitesImpl = async () => {
        throw new ApiClientError({ status: 500, code: "internal_error", message: "boom", requestId: "req-invites", url: "http://example.test/rbac/tenant-invites", method: "GET" });
    };
    renderWithQueryClient(<Page />);
    await waitForList();
    await screen.findByText("Could not load the invites.", undefined, { timeout: SLOW });
    assert.ok(screen.getByText(/Request reference: req-invites/));
    assert.equal(rows().length, 5, "the member list still shows");
    assert.equal(screen.queryAllByText("No open invites.").length, 0, "a failed read is not an empty list");

    const readsBefore = invitesReads;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => assert.equal(invitesReads > readsBefore, true, "Try again reads the invites again"), { timeout: SLOW });
});
