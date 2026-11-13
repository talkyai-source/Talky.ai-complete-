import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { useLayoutEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { AUTH_IDENTITY_EVENT_KEY, AuthProvider, useAuth } from "./auth-context";
import { api } from "./api";
import { backendApi } from "./backend-api";
import { createAppQueryClient } from "./app-query-client";
import { clearFreshLoginGrace } from "./http-client";
import { emailAuditStore, resetEmailAuditStoreForTests } from "./email-audit";
import { useEmailAuditActions, useEmailAuditState } from "./email-audit-client";
import { notificationsStore } from "./notifications";
import { NotificationsIdentityProvider } from "./notifications-client";
import { useSendEmail } from "./api-hooks";

const accountA = {
    id: "email-user-a", tenant_id: "email-tenant-a", email: "a@example.invalid",
    role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;
const accountB = { ...accountA, id: "email-user-b", tenant_id: "email-tenant-b", email: "b@example.invalid" };
const recipientA = "private-a@example.invalid";
const recipientB = "private-b@example.invalid";
const originalGetMe = api.getMe;
const originalLogout = api.logout;
let auth: ReturnType<typeof useAuth>;
let actions: ReturnType<typeof useEmailAuditActions>;
let send: ReturnType<typeof useSendEmail>;
let queryClient: QueryClient;

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
function Probe() {
    const currentAuth = useAuth();
    const currentActions = useEmailAuditActions();
    const currentSend = useSendEmail();
    const state = useEmailAuditState();
    useLayoutEffect(() => { auth = currentAuth; actions = currentActions; send = currentSend; });
    return <div><span>{currentAuth.user?.id ?? "anonymous"}</span>
        {state.items.map(item => <p key={item.id}>{item.to.join(",")}: {item.status}</p>)}</div>;
}
async function mountA() {
    api.getMe = async () => ({ ...accountA });
    render(<QueryClientProvider client={queryClient}><AuthProvider>
        <NotificationsIdentityProvider /><Probe />
    </AuthProvider></QueryClientProvider>);
    await screen.findByText(accountA.id);
}
function switchTo(account: typeof accountA) {
    auth.applyLoginResult({ user_id: account.id, tenant_id: account.tenant_id,
        email: account.email, role: account.role, access_token: "synthetic-" + account.id });
}
function attempt(recipient = recipientA) {
    return actions.createAttempt({ to: [recipient], templateId: "synthetic-template", subject: "Private email" });
}
function marker(kind: "changed" | "logout") {
    window.dispatchEvent(new window.StorageEvent("storage", {
        key: AUTH_IDENTITY_EVENT_KEY, newValue: JSON.stringify({ kind, nonce: crypto.randomUUID() }),
        storageArea: window.localStorage,
    }));
}
function savedText() {
    return Array.from({ length: window.localStorage.length }, (_, index) =>
        window.localStorage.getItem(window.localStorage.key(index)!)).join("\n");
}
beforeEach(() => {
    window.localStorage.clear();
    clearFreshLoginGrace();
    resetEmailAuditStoreForTests();
    notificationsStore.setIdentity(null);
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external email/auth request"); });
    queryClient = createAppQueryClient(() => {});
    queryClient.setDefaultOptions({ queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 } });
});
afterEach(() => {
    cleanup();
    queryClient.clear();
    api.getMe = originalGetMe;
    api.logout = originalLogout;
    mock.restoreAll();
    resetEmailAuditStoreForTests();
    notificationsStore.setIdentity(null);
    window.localStorage.clear();
    clearFreshLoginGrace();
});

test("real AuthProvider account switches retire old audit actions even after A to B to A", async () => {
    await mountA();
    let idA!: string;
    act(() => { idA = attempt(); });
    const old = actions;
    act(() => {
        switchTo(accountB);
        assert.deepEqual(emailAuditStore.getSnapshot().items, [], "Old history must disappear synchronously");
    });
    act(() => { attempt(recipientB); });
    assert.ok(!old.exportHistoryJson().includes(recipientB));
    act(() => { old.clearAll(); });
    assert.equal(emailAuditStore.getSnapshot().items.length, 1);
    act(() => { switchTo(accountA); });
    assert.equal(emailAuditStore.getSnapshot().items[0]?.id, idA);
    act(() => { old.markSuccess(idA, { messageId: "stale-a-result" }); old.clearAll(); });
    assert.equal(emailAuditStore.getSnapshot().items[0]?.status, "pending");
    assert.equal(emailAuditStore.getSnapshot().items[0]?.id, idA);
    assert.throws(() => old.createAttempt({ to: [recipientB], templateId: "stale" }), /identity|account|sign.in/i);
    assert.ok(!old.exportHistoryJson().includes(recipientA));
});

for (const next of [accountA, accountB]) test(`cross-tab verification hides history before /me and resumes only ${next.id}`, async () => {
    await mountA();
    let id!: string;
    act(() => { id = attempt(); });
    const old = actions;
    const verifying = deferred<typeof accountA>();
    api.getMe = () => verifying.promise;
    act(() => {
        marker("changed");
        assert.deepEqual(emailAuditStore.getSnapshot().items, [], "Do not wait for a React effect or /me");
    });
    assert.equal(auth.user, null);
    assert.ok(!old.exportHistoryJson().includes(recipientA));
    await act(async () => { verifying.resolve(next); await verifying.promise; });
    await waitFor(() => assert.equal(auth.user?.id, next.id));
    assert.equal(emailAuditStore.getSnapshot().items.length, next.id === accountA.id ? 1 : 0);
    if (next.id === accountA.id) assert.equal(emailAuditStore.getSnapshot().items[0]?.id, id);
});

for (const silent of [true, false]) test(`verified same-user refresh preserves email history and action ownership (silent=${silent})`, async () => {
    await mountA();
    let id!: string;
    act(() => { id = attempt(); });
    const prior = actions;
    await act(async () => { await auth.refreshUser({ silent }); });
    assert.equal(emailAuditStore.getSnapshot().items[0]?.id, id);
    act(() => { prior.markSuccess(id, { messageId: "same-owner" }); });
    assert.equal(emailAuditStore.getSnapshot().items[0]?.messageId, "same-owner");
    assert.ok(actions.exportHistoryJson().includes(recipientA));
});

for (const remote of [false, true]) test(`logout clears only the retiring scope before server completion (remote=${remote})`, async () => {
    await mountA();
    act(() => { switchTo(accountB); });
    act(() => { attempt(recipientB); });
    act(() => { switchTo(accountA); });
    act(() => { attempt(); });
    const ending = deferred<void>();
    api.logout = () => ending.promise;
    let pending: ReturnType<typeof auth.logout> | undefined;
    act(() => {
        if (remote) marker("logout"); else pending = auth.logout();
        assert.deepEqual(emailAuditStore.getSnapshot().items, []);
        assert.ok(!savedText().includes(recipientA), "A's saved recipient data must be removed at logout start");
    });
    assert.ok(savedText().includes(recipientB), "Another known scope must not be erased");
    assert.ok(!actions.exportHistoryJson().includes(recipientB));
    await act(async () => { ending.resolve(); if (pending) await pending; });
});

test("logging out a verified tenantless administrator cannot erase the previous tenant user's history", async () => {
    await mountA();
    let id!: string;
    act(() => { id = attempt(); });
    const admin = { ...accountB, id: "tenantless-admin", tenant_id: undefined, role: "platform_admin" };
    api.getMe = async () => admin;
    api.logout = async () => {};
    await act(async () => {
        auth.applyLoginResult({ user_id: admin.id, email: admin.email, role: admin.role,
            access_token: "synthetic-tenantless-admin" });
    });
    await waitFor(() => assert.equal(auth.user?.id, admin.id));
    assert.deepEqual(emailAuditStore.getSnapshot().items, []);
    assert.ok(savedText().includes(recipientA));
    await act(async () => { await auth.logout(); });
    assert.ok(savedText().includes(recipientA), "Logging out tenantless B must not delete tenant A's saved key");
    act(() => { emailAuditStore.setIdentity({ tenantId: accountA.tenant_id, userId: accountA.id }); });
    assert.equal(emailAuditStore.getSnapshot().items[0]?.id, id);
});

for (const failure of [false, true]) test(`late send completion cannot update email history after A to B to A (failure=${failure})`, async () => {
    const result = deferred<Awaited<ReturnType<typeof backendApi.email.send>>>();
    const started = deferred<void>();
    mock.method(backendApi.email, "send", async () => { started.resolve(); return result.promise; });
    await mountA();
    let pending!: ReturnType<typeof send.mutateAsync>;
    let checked!: Promise<unknown>;
    await act(async () => {
        pending = send.mutateAsync({ to: [recipientA], templateId: "synthetic-template", html: "<p>Synthetic</p>" });
        checked = failure ? assert.rejects(pending, /synthetic A failure/) : pending;
        await started.promise;
    });
    const id = emailAuditStore.getSnapshot().items[0]?.id;
    assert.ok(id);
    act(() => { switchTo(accountB); });
    act(() => { switchTo(accountA); });
    await act(async () => {
        if (failure) result.reject(new Error("synthetic A failure"));
        else result.resolve({ messageId: "late-a-message", status: "sent" });
        await checked;
    });
    assert.equal(emailAuditStore.getSnapshot().items[0]?.id, id);
    assert.equal(emailAuditStore.getSnapshot().items[0]?.status, "pending");
    assert.equal(emailAuditStore.getSnapshot().items[0]?.messageId, undefined);
});
