/**
 * FX2a Task 3 — the remaining browser pop-ups ask in the app's own dialogs:
 * "Sign out from all other devices", removing a passkey and forgetting
 * telephony credentials. Each test replaces window.confirm with a counter
 * that must stay at zero.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import DeviceList from "@/components/auth/device-list";
import PasskeyList from "@/components/auth/passkey-list";
import { TelephonyProvidersSection } from "@/components/settings/telephony-providers-section";
import { api } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";

const SLOW = 15_000;
const originalConfirm = window.confirm;
const clients: QueryClient[] = [];
let popups = 0;

beforeEach(() => {
    popups = 0;
    window.confirm = () => { popups += 1; return true; };
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    notificationsStore.setIdentity(null);
    window.confirm = originalConfirm;
});

// ───────── Sign out from all other devices ─────────

const SESSIONS = {
    sessions: [
        { id: "s-1", device_name: "This laptop", device_type: "desktop", is_current: true, last_active_at: "2026-11-10T00:00:00Z" },
        { id: "s-2", device_name: "Phone", device_type: "mobile", is_current: false, last_active_at: "2026-11-09T00:00:00Z" },
    ],
};
const ALL = "Sign out from all other devices";

async function openSignOutDialog() {
    mock.method(api, "getActiveSessions", async () => SESSIONS as unknown as Awaited<ReturnType<typeof api.getActiveSessions>>);
    const revoke = mock.method(api, "revokeSession", async () => undefined as unknown as Awaited<ReturnType<typeof api.revokeSession>>);
    render(<DeviceList token="" />);
    fireEvent.click(await screen.findByRole("button", { name: ALL }, { timeout: SLOW }));
    return { dialog: within(await screen.findByRole("dialog")), revoke };
}

test("Sign out from all other devices asks in the app's confirm dialog; Cancel signs nothing out", async () => {
    const { dialog, revoke } = await openSignOutDialog();
    assert.equal(dialog.queryByText("Are you sure you want to sign out from all other devices?") !== null, true, "the existing confirm sentence is the body");
    assert.equal(dialog.queryAllByText(ALL).length, 2, "title and confirm button carry the opener's label");
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(revoke.mock.callCount(), 0);
    assert.equal(popups, 0, "no browser confirm()");
});

test("Sign out from all other devices: confirming in the dialog revokes the other sessions", async () => {
    const { dialog, revoke } = await openSignOutDialog();
    fireEvent.click(dialog.getByRole("button", { name: ALL }));
    await waitFor(() => assert.equal(revoke.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(revoke.mock.calls[0]!.arguments, ["s-2"]);
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(popups, 0, "no browser confirm()");
});

// ───────── Remove passkey ─────────

const PASSKEY = { id: "pk-1", credential_id: "cred", display_name: "Work key", device_type: "platform", backed_up: false, transports: [], created_at: "2026-01-01T00:00:00Z" };

async function openPasskeyDialog() {
    mock.method(api, "listPasskeys", async () => [PASSKEY]);
    const remove = mock.method(api, "deletePasskey", async () => undefined);
    render(<PasskeyList />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove passkey Work key" }, { timeout: SLOW }));
    return { dialog: within(await screen.findByRole("dialog")), remove };
}

test("removing a passkey asks in the app's confirm dialog; Cancel removes nothing", async () => {
    const { dialog, remove } = await openPasskeyDialog();
    assert.equal(dialog.queryByText('Remove passkey "Work key"?') !== null, true, "the existing confirm sentence is the body");
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(remove.mock.callCount(), 0);
    assert.equal(popups, 0, "no browser confirm()");
});

test("removing a passkey: confirming in the dialog deletes it", async () => {
    const { dialog, remove } = await openPasskeyDialog();
    fireEvent.click(dialog.getByRole("button", { name: "Remove passkey Work key" }));
    await waitFor(() => assert.equal(remove.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(remove.mock.calls[0]!.arguments, ["pk-1"]);
    await waitFor(() => assert.equal(screen.queryByText("Work key") === null, true), { timeout: SLOW });
    assert.equal(popups, 0, "no browser confirm()");
});

// ───────── Forget telephony credentials ─────────

function mountTelephony() {
    const availability = { activation_allowed: false, qualification_only: false, reason_code: "cloud_telephony_unavailable", reason: "Cloud calling is unavailable in production. Credentials remain saved." };
    const data = {
        active: "none", availability: { twilio: availability, vonage: availability },
        providers: ["twilio", "vonage"].map((provider) => ({ provider, status: "active", has_credentials: true, last_test_result: { ok: true, check_scope: "provider_account" } })),
    };
    const writes: Parameters<typeof api.request>[0][] = [];
    notificationsStore.setIdentity({ tenantId: "cloud-ui", userId: "owner" });
    mock.method(api, "request", async (options: Parameters<typeof api.request>[0]) => {
        if (options.method && options.method !== "GET") { writes.push(options); return {}; }
        if (options.path === "/telephony/providers") return data;
        if (options.path.endsWith("/pool-assignment")) return {};
        return [];
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><TelephonyProvidersSection /></QueryClientProvider>);
    return writes;
}

async function openForgetDialog() {
    const writes = mountTelephony();
    const card = (await screen.findByRole("heading", { name: "Twilio" }, { timeout: SLOW })).closest("div.rounded-xl") as HTMLElement;
    const trash = within(card).getAllByRole("button").find((button) => (button.textContent ?? "").trim() === "")!;
    fireEvent.click(trash);
    return { dialog: within(await screen.findByRole("dialog")), writes };
}

test("forgetting telephony credentials asks in the app's confirm dialog; Cancel sends nothing", async () => {
    const { dialog, writes } = await openForgetDialog();
    assert.equal(dialog.queryByText("Forget Twilio credentials for this tenant?") !== null, true, "the existing confirm sentence is the body");
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(writes.length, 0);
    assert.equal(popups, 0, "no browser confirm()");
});

test("forgetting telephony credentials: confirming in the dialog sends the delete", async () => {
    const { dialog, writes } = await openForgetDialog();
    fireEvent.click(dialog.getByRole("button", { name: "Delete" }));
    await waitFor(() => assert.equal(writes.some((write) => write.method === "DELETE"), true), { timeout: SLOW });
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(popups, 0, "no browser confirm()");
});
