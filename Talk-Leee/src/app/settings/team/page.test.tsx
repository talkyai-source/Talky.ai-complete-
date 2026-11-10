import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ApiClientError } from "@/lib/http-client";
import { teamApi, type TeamInvite, type TeamMember } from "@/lib/team-api";

// QA F1-15 (QA-UI-151): team management (/rbac/tenant-users) had no screen.
// Settings → Team lists the workspace's members; admins can change another
// member's role or remove them, after a confirmation. is_primary marks a
// member's home workspace (one per person), so nearly every member has it:
// it is not ownership, and it never hides the controls.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let role = "tenant_admin";
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u-admin", email: "Admin@Example.test", role, account_timezone: "Europe/London" } }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); mock.restoreAll(); clients.splice(0).forEach((client) => client.clear()); role = "tenant_admin"; });

const OLIVE: TeamMember = { id: "m-olive", email: "olive@example.test", name: "Olive Admin", role: "tenant_admin", is_primary: true, status: "active", joined_at: "2026-09-01T09:00:00Z" };
const ME: TeamMember = { id: "m-me", email: "admin@example.test", name: "Ada Admin", role: "tenant_admin", is_primary: true, status: "active", joined_at: "2026-10-01T09:00:00Z" };
const AGENT: TeamMember = { id: "m-agent", email: "sam@example.test", name: "Sam Agent", role: "agent", is_primary: true, status: "active", joined_at: "2026-10-15T09:00:00Z" };

function mount(members: () => Promise<TeamMember[]>, invites: () => Promise<TeamInvite[]> = async () => []) {
    const list = mock.method(teamApi, "list", members);
    mock.method(teamApi, "listInvites", invites);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
    return list;
}
const none = (role: "button" | "combobox", name: RegExp) => screen.queryAllByRole(role, { name }).length === 0;

test("everyone sees the members, their roles, and which one is them", async () => {
    role = "readonly";
    mount(async () => [OLIVE, ME, AGENT]);
    await screen.findByText("Sam Agent", undefined, { timeout: SLOW });
    assert.ok(screen.getByText("You"));
    assert.equal(screen.queryAllByText("Owner").length, 0, "a home-workspace flag is shown as ownership");
    assert.ok(screen.getByText(/sam@example\.test · Agent/));
    assert.equal(none("combobox", /^Role for/), true, "a read-only role is offered role changes");
    assert.equal(none("button", /^Remove/), true, "a read-only role is offered removal");
});

test("an admin changes another member's role only after confirming", async () => {
    const list = mount(async () => [OLIVE, ME, AGENT]);
    const change = mock.method(teamApi, "changeRole", async () => undefined);
    fireEvent.click(await screen.findByRole("combobox", { name: "Role for Sam Agent" }, { timeout: SLOW }));
    fireEvent.click(await screen.findByRole("option", { name: "Read only" }));
    let dialog = within(await screen.findByRole("dialog"));
    assert.ok(dialog.getByText("Sam Agent will become Read only."));
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    assert.equal(change.mock.callCount(), 0);
    fireEvent.click(screen.getByRole("combobox", { name: "Role for Sam Agent" }));
    fireEvent.click(await screen.findByRole("option", { name: "Read only" }));
    dialog = within(await screen.findByRole("dialog"));
    await act(async () => { fireEvent.click(dialog.getByRole("button", { name: "Change role" })); });
    await waitFor(() => assert.equal(change.mock.callCount(), 1));
    assert.deepEqual(change.mock.calls[0].arguments, ["m-agent", "readonly"]);
    await waitFor(() => assert.ok(list.mock.callCount() >= 2, "the list is read again"));
});

test("an admin removes another member after confirming; only their own row offers neither", async () => {
    mount(async () => [OLIVE, ME, AGENT]);
    const remove = mock.method(teamApi, "remove", async () => undefined);
    await screen.findByText("Sam Agent", undefined, { timeout: SLOW });
    assert.equal(none("combobox", /Role for Ada Admin/), true, "own role change offered");
    assert.equal(none("button", /Remove Ada Admin/), true, "own removal offered");
    assert.equal(none("combobox", /Role for Olive Admin/), false, "another admin's role is not offered");
    assert.equal(none("button", /Remove Olive Admin/), false, "another admin's removal is not offered");
    fireEvent.click(screen.getByRole("button", { name: "Remove Sam Agent" }));
    const dialog = within(await screen.findByRole("dialog"));
    assert.ok(dialog.getByText("Sam Agent will lose access to this workspace."));
    await act(async () => { fireEvent.click(dialog.getByRole("button", { name: "Remove" })); });
    await waitFor(() => assert.equal(remove.mock.callCount(), 1));
    assert.deepEqual(remove.mock.calls[0].arguments, ["m-agent"]);
});

test("a change the server refuses stays open and says why", async () => {
    const list = mount(async () => [OLIVE, ME, AGENT]);
    const refusal = "This is the workspace's last active administrator. Make someone else an administrator first.";
    mock.method(teamApi, "changeRole", async () => {
        throw new ApiClientError({ status: 409, code: "http_error", message: refusal, url: "http://example.test/rbac/tenant-users/m-olive", method: "PATCH" });
    });
    fireEvent.click(await screen.findByRole("combobox", { name: "Role for Olive Admin" }, { timeout: SLOW }));
    fireEvent.click(await screen.findByRole("option", { name: "Read only" }));
    const dialog = within(await screen.findByRole("dialog"));
    await act(async () => { fireEvent.click(dialog.getByRole("button", { name: "Change role" })); });
    await waitFor(() => assert.equal(dialog.queryAllByText(refusal).length, 1));
    assert.equal(screen.queryAllByRole("dialog").length, 1, "the dialog closed on a refusal");
    assert.equal(list.mock.callCount(), 1, "the list was re-read as if the change happened");
});

test("a failed read is a load error with a retry, never an empty team", async () => {
    mount(async () => { throw new ApiClientError({ status: 500, code: "internal_error", message: "boom", requestId: "req-team", url: "http://example.test/rbac/tenant-users", method: "GET" }); });
    await screen.findByText("Could not load the team.", undefined, { timeout: SLOW });
    assert.ok(screen.getByText(/Request reference: req-team/));
    assert.equal(screen.queryByText("No members to show."), null);
});

test("Settings links to the Team page", () => {
    const settings = readFileSync(path.join(process.cwd(), "src/app/settings/page.tsx"), "utf8");
    assert.match(settings, /href="\/settings\/team"/);
});

// D4-2 (owner decisions, 11 Nov 2026): admins invite by email; the link
// works for 7 days and once; an existing account is refused by the server.
const WAITING: TeamInvite = { id: "inv-1", email: "new@example.com", role: "agent", created_at: "2026-11-11T09:00:00Z", expires_at: "2026-11-18T09:00:00Z" };

async function fillInvite(email: string, roleLabel: string) {
    fireEvent.change(await screen.findByLabelText("Email", undefined, { timeout: SLOW }), { target: { value: email } });
    fireEvent.click(screen.getByRole("combobox", { name: "Role for the new person" }));
    fireEvent.click(await screen.findByRole("option", { name: roleLabel }));
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send invite" })); });
}

test("an admin invites someone by email with a role", async () => {
    let reads = 0;
    mount(async () => [ME, AGENT], async () => { reads += 1; return []; });
    const invite = mock.method(teamApi, "invite", async () => ({ ...WAITING, email_sent: true }));
    await fillInvite("  New@Example.com ", "Agent");
    await waitFor(() => assert.equal(invite.mock.callCount(), 1));
    assert.deepEqual(invite.mock.calls[0].arguments, ["new@example.com", "agent"]);
    assert.equal(screen.getAllByText("Invite sent to new@example.com.").length, 1);
    await waitFor(() => assert.ok(reads >= 2, "the waiting list is read again"));
    assert.equal(screen.queryAllByText(/not available yet/).length, 0);
});

test("an email that did not go out says so, and Resend sends a new link", async () => {
    mount(async () => [ME, AGENT], async () => [WAITING]);
    const invite = mock.method(teamApi, "invite", async () => ({ ...WAITING, email_sent: false }));
    fireEvent.click(await screen.findByRole("button", { name: "Resend the invite to new@example.com" }, { timeout: SLOW }));
    await waitFor(() => assert.equal(invite.mock.callCount(), 1));
    assert.deepEqual(invite.mock.calls[0].arguments, ["new@example.com", "agent"]);
    assert.equal(screen.getAllByText(/was saved, but the email did not go out/).length, 1);
});

test("a refused invite says why", async () => {
    mount(async () => [ME, AGENT]);
    const refusal = "This email already has an account, so it cannot be invited.";
    mock.method(teamApi, "invite", async () => {
        throw new ApiClientError({ status: 409, code: "http_error", message: refusal, url: "http://example.test/rbac/tenant-invites", method: "POST" });
    });
    await fillInvite("sam@example.com", "Read only");
    await waitFor(() => assert.equal(screen.queryAllByRole("alert").some((node) => node.textContent === refusal), true));
});

test("a mistyped email is caught before anything is sent", async () => {
    mount(async () => [ME, AGENT]);
    const invite = mock.method(teamApi, "invite", async () => ({ ...WAITING, email_sent: true }));
    await fillInvite("not-an-email", "Agent");
    assert.equal(screen.queryAllByRole("alert").some((node) => node.textContent === "Enter a valid email address."), true);
    assert.equal(invite.mock.callCount(), 0);
});

test("withdrawing an invite asks first", async () => {
    mount(async () => [ME, AGENT], async () => [WAITING]);
    const withdraw = mock.method(teamApi, "withdrawInvite", async () => undefined);
    fireEvent.click(await screen.findByRole("button", { name: "Withdraw the invite to new@example.com" }, { timeout: SLOW }));
    const dialog = within(await screen.findByRole("dialog"));
    assert.ok(dialog.getByText("The invite link sent to new@example.com will stop working."));
    await act(async () => { fireEvent.click(dialog.getByRole("button", { name: "Withdraw" })); });
    await waitFor(() => assert.equal(withdraw.mock.callCount(), 1));
    assert.deepEqual(withdraw.mock.calls[0].arguments, ["inv-1"]);
});

test("someone who is not an admin sees no invite form and never asks for invites", async () => {
    role = "agent";
    let reads = 0;
    mount(async () => [ME, AGENT], async () => { reads += 1; return []; });
    await screen.findByText("Sam Agent", undefined, { timeout: SLOW });
    assert.equal(screen.queryAllByRole("heading", { name: "Invite someone" }).length, 0);
    assert.equal(reads, 0);
});
