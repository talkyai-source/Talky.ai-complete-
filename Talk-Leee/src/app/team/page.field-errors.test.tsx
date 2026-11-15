/**
 * FX4a Task 3 — on the Team page a field's error is tied to that field: the
 * error has an id, the field points to it with aria-describedby and carries
 * aria-invalid="true" while the error shows. This covers the invite form's
 * Email field and a row's Role field. An error that does not belong to a field
 * (a refused removal, a failed resend) marks no field.
 */
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


const SLOW = 20_000;

const TENANT_ADMIN = { id: "user-admin", email: "admin@example.test", role: "tenant_admin", tenant_id: "tenant-a" };

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
let invitesImpl: () => Promise<TeamInvite[]> = async () => [];
function mockInvites() {
    return mock.method(teamApi, "listInvites", async () => invitesImpl());
}
beforeEach(() => {
    invitesImpl = async () => [];
    mockInvites();
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    currentUser = null;
});

const row = (id: string) => within(screen.getAllByTestId("team-member-row").find((el) => el.getAttribute("data-member-id") === id)!);
const waitForList = () => waitFor(() => assert.ok(screen.getByTestId("team-member-list")), { timeout: SLOW });

async function openRoleMenu(email: string) {
    const trigger = screen.getByRole("combobox", { name: `Role: ${email}` });
    fireEvent.click(trigger);
    await waitFor(() => assert.ok(screen.getByRole("listbox")), { timeout: SLOW });
    return screen.getAllByRole("option").map((o) => o.textContent ?? "");
}
const WAITING: TeamInvite = { id: "inv-1", email: "new@example.com", role: "agent", created_at: "2026-11-11T09:00:00Z", expires_at: "2026-11-18T09:00:00Z" };

async function fillInvite(email: string, roleLabel: string) {
    fireEvent.change(await screen.findByLabelText("Email", undefined, { timeout: SLOW }), { target: { value: email } });
    fireEvent.click(screen.getByRole("combobox", { name: "Role for the new person" }));
    fireEvent.click(await screen.findByRole("option", { name: roleLabel }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send invite" })); });
}

const emailField = async () => await screen.findByLabelText("Email", undefined, { timeout: SLOW }) as HTMLInputElement;
const alertWith = (text: string) => screen.queryAllByRole("alert").find((node) => node.textContent === text);
/** The field is invalid and described by exactly the element that shows `text`. */
function tiedTo(field: HTMLElement, text: string) {
    assert.equal(field.getAttribute("aria-invalid"), "true");
    const id = field.getAttribute("aria-describedby");
    assert.equal(typeof id === "string" && id.length > 0, true, "the field points to an error");
    const described = document.getElementById(id!);
    assert.equal(described !== null, true, "the id exists");
    assert.equal(described!.textContent, text);
}
function untied(field: HTMLElement) {
    assert.equal(field.getAttribute("aria-invalid") === null, true, "not invalid");
    assert.equal(field.getAttribute("aria-describedby") === null, true, "points to nothing");
}

test("invite: a mistyped email is the Email field's error", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    const invite = mock.method(teamApi, "invite", async () => ({ ...WAITING, email_sent: true }));
    renderWithQueryClient(<Page />);
    untied(await emailField());
    await fillInvite("not-an-email", "Agent");
    tiedTo(await emailField(), "Enter a valid email address.");
    assert.equal(invite.mock.callCount(), 0);
});

test("invite: the server's refusal of the address is the Email field's error; a sent invite clears it", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    const refusal = "This email already has an account, so it cannot be invited.";
    let refuse = true;
    mock.method(teamApi, "invite", async () => {
        if (refuse) throw new ApiClientError({ status: 409, code: "http_error", message: refusal, url: "http://example.test/rbac/tenant-invites", method: "POST" });
        return { ...WAITING, email_sent: true };
    });
    renderWithQueryClient(<Page />);
    await fillInvite("sam@example.com", "Agent");
    await waitFor(() => assert.equal(alertWith(refusal) !== undefined, true), { timeout: SLOW });
    tiedTo(await emailField(), refusal);

    refuse = false;
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send invite" })); });
    await waitFor(() => assert.equal(screen.queryAllByText("Invite sent to new@example.com.").length, 1), { timeout: SLOW });
    untied(await emailField());
});

test("invite: a failure that is not about the address marks no field", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    // A resend that fails is about that invite, not about what is typed in the form.
    invitesImpl = async () => [WAITING];
    const refusal = "This email already has an account, so it cannot be invited.";
    mock.method(teamApi, "invite", async () => {
        throw new ApiClientError({ status: 409, code: "http_error", message: refusal, url: "http://example.test/rbac/tenant-invites", method: "POST" });
    });
    renderWithQueryClient(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: "Resend the invite to new@example.com" }, { timeout: SLOW }));
    await waitFor(() => assert.equal(alertWith(refusal) !== undefined, true), { timeout: SLOW });
    untied(await emailField());
    cleanup();

    // The server did not answer with a refusal (a 500): not the Email field's error either.
    mock.restoreAll();
    mockInvites();
    invitesImpl = async () => [];
    mockMembers();
    mockRoles();
    mock.method(teamApi, "invite", async () => {
        throw new ApiClientError({ status: 500, code: "http_error", message: "Server error", url: "http://example.test/rbac/tenant-invites", method: "POST" });
    });
    renderWithQueryClient(<Page />);
    await fillInvite("sam@example.com", "Agent");
    await waitFor(() => assert.equal(alertWith("Server error") !== undefined, true), { timeout: SLOW });
    untied(await emailField());
});

test("a refused role change is that row's Role field's error", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers({ active: [...ACTIVE, OLIVE_ROW], pending: [], suspended: [] });
    mockRoles();
    const refusal = "Cannot grant a role above your own tier";
    mock.method(backendApi.team, "updateMember", async () => { throw new Error(refusal); });
    renderWithQueryClient(<Page />);
    await waitForList();
    await waitFor(() => assert.ok(screen.getByRole("combobox", { name: "Role: casey@example.test" })), { timeout: SLOW });
    untied(screen.getByRole("combobox", { name: "Role: casey@example.test" }));

    await openRoleMenu("casey@example.test");
    fireEvent.click(screen.getByRole("option", { name: "Tenant admin" }));
    const dialog = await screen.findByRole("dialog", {}, { timeout: SLOW });
    fireEvent.click(within(dialog).getByRole("button", { name: "Change role" }));
    await waitFor(() => assert.ok(row("tu-2").getByTestId("team-member-error")), { timeout: SLOW });
    const field = screen.getByRole("combobox", { name: "Role: casey@example.test" });
    tiedTo(field, refusal);
    assert.equal(field.getAttribute("aria-describedby"), row("tu-2").getByTestId("team-member-error").id);
    // Another row's Role field is untouched.
    untied(screen.getByRole("combobox", { name: "Role: olive@example.test" }));
});

test("a refused removal shows on the row but marks no field", async () => {
    currentUser = TENANT_ADMIN;
    mockMembers();
    mockRoles();
    mock.method(backendApi.team, "removeMember", async () => { throw new Error("Cannot remove yourself as primary tenant member"); });
    renderWithQueryClient(<Page />);
    await waitForList();
    fireEvent.click(row("tu-2").getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog", {}, { timeout: SLOW });
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => assert.ok(row("tu-2").getByTestId("team-member-error")), { timeout: SLOW });
    untied(screen.getByRole("combobox", { name: "Role: casey@example.test" }));
});

// FX4a2 Task 1 — only a refusal about the invite's email marks the Email field:
// 422 (backend rbac/schemas.py:77, EmailStr) and 409 (tenant_invites.py:124-126).
test("invite: a permission refusal, too many requests and a network failure mark no field; a 422 marks Email", async () => {
    const refusals: Array<[number, string]> = [
        [401, "Not authenticated"],
        [403, "Cannot grant a role above your own tier"],
        [429, "Too many attempts. Please try again later."],
        [503, "Invites are unavailable right now. Try again shortly."],
    ];
    for (const [status, message] of refusals) {
        currentUser = TENANT_ADMIN;
        mockInvites();
        mockMembers();
        mockRoles();
        mock.method(teamApi, "invite", async () => {
            throw new ApiClientError({ status, code: "http_error", message, url: "http://example.test/rbac/tenant-invites", method: "POST" });
        });
        renderWithQueryClient(<Page />);
        await fillInvite("sam@example.com", "Agent");
        await waitFor(() => assert.equal(alertWith(message) !== undefined, true, `${status} shows`), { timeout: SLOW });
        untied(await emailField());
        cleanup();
        mock.restoreAll();
    }

    // No answer at all (network failure): the existing sentence, no field.
    currentUser = TENANT_ADMIN;
    mockInvites();
    mockMembers();
    mockRoles();
    mock.method(teamApi, "invite", async () => { throw new TypeError("Failed to fetch"); });
    renderWithQueryClient(<Page />);
    await fillInvite("sam@example.com", "Agent");
    await waitFor(() => assert.equal(alertWith("Could not send the invite. Try again.") !== undefined, true), { timeout: SLOW });
    untied(await emailField());
    cleanup();
    mock.restoreAll();

    // The server's own email validation (422) is the Email field's error.
    currentUser = TENANT_ADMIN;
    mockInvites();
    mockMembers();
    mockRoles();
    const invalid = "value is not a valid email address";
    mock.method(teamApi, "invite", async () => {
        throw new ApiClientError({ status: 422, code: "http_error", message: invalid, url: "http://example.test/rbac/tenant-invites", method: "POST" });
    });
    renderWithQueryClient(<Page />);
    await fillInvite("sam@example.com", "Agent");
    await waitFor(() => assert.equal(alertWith(invalid) !== undefined, true), { timeout: SLOW });
    tiedTo(await emailField(), invalid);
});
