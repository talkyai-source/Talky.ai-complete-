/**
 * FX2b Task 7.2, 7.3, 7.5 — Settings and Security panels use the shared
 * states: telephony loads in the LoadingState and fails in the ErrorState
 * with Retry; "No PBX connected yet." and "No active devices" are the shared
 * EmptyState; the passkey list loads and fails in the shared states.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import DeviceList from "@/components/auth/device-list";
import PasskeyList from "@/components/auth/passkey-list";
import { TelephonyProvidersSection } from "@/components/settings/telephony-providers-section";
import { api } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";

const SLOW = 15_000;
const realRequest = api.request;
const clients: QueryClient[] = [];
afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    api.request = realRequest;
    notificationsStore.setIdentity(null);
});
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const status = (title: string) => screen.queryAllByRole("status").some((node) => within(node).queryByText(title) !== null);
type Options = Parameters<typeof api.request>[0];
const providersAnswer = { active: "none", availability: {}, providers: [] };

function mountTelephony(providers: () => Promise<unknown>) {
    const counts = { providers: 0 };
    notificationsStore.setIdentity({ tenantId: "states-ui", userId: "owner" });
    api.request = async <T,>(options: Options) => {
        if (options.path === "/telephony/providers") { counts.providers += 1; return await providers() as T; }
        if (options.path.endsWith("/pool-assignment")) return {} as T;
        if (options.path.endsWith("/outbound-route")) return { ready: true, reason_code: "sip_not_required" } as T;
        return [] as T;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><TelephonyProvidersSection /></QueryClientProvider>);
    return counts;
}

test("telephony: loading is a status titled 'Loading telephony settings…'", async () => {
    let answer: (() => void) | null = null;
    mountTelephony(() => new Promise((resolve) => { answer = () => resolve(providersAnswer); }));
    await waitFor(() => assert.equal(status("Loading telephony settings…"), true), { timeout: SLOW });
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("Loading telephony settings…"), false), { timeout: SLOW });
});

test("telephony: a failed read shows 'Failed to load telephony settings' with Retry, which reads again", async () => {
    let fail = true;
    const counts = mountTelephony(async () => { if (fail) throw new Error("upstream said: boom"); return providersAnswer; });
    const sentence = await screen.findByText("Failed to load telephony settings", undefined, { timeout: SLOW });
    assert.equal(sentence.getAttribute("role"), "alert");
    assert.equal(shown("upstream said: boom"), false, "the panel's own sentence is the message");
    fail = false;
    const before = counts.providers;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Failed to load telephony settings"), false), { timeout: SLOW });
    assert.equal(counts.providers, before + 1);
});

test("telephony: with no PBX the shared EmptyState shows 'No PBX connected yet.' and its sentence", async () => {
    mountTelephony(async () => providersAnswer);
    const title = await screen.findByText("No PBX connected yet.", undefined, { timeout: SLOW });
    assert.equal(title.tagName, "H2", "the shared EmptyState title");
    assert.equal(shown("Select Connect PBX and enter the address, username and password your provider gave you."), true);
});

test("devices: no sessions shows 'No active devices' in the shared EmptyState", async () => {
    mock.method(api, "getActiveSessions", async () => ({ sessions: [] }));
    render(<DeviceList token="" />);
    const title = await screen.findByText("No active devices", undefined, { timeout: SLOW });
    assert.equal(title.tagName, "H2", "the shared EmptyState title");
});

type Passkeys = Awaited<ReturnType<typeof api.listPasskeys>>;
const passkey = {
    id: "pk-1", credential_id: "cred", display_name: "Laptop key", device_type: "platform", backed_up: false,
    transports: [], created_at: "2026-01-01T00:00:00Z",
};

test("passkeys: loading is a status titled 'Loading passkeys…'", async () => {
    let answer: (() => void) | null = null;
    mock.method(api, "listPasskeys", () => new Promise<Passkeys>((resolve) => { answer = () => resolve([passkey] as Passkeys); }));
    render(<PasskeyList />);
    assert.equal(status("Loading passkeys…"), true);
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("Laptop key"), true), { timeout: SLOW });
});

test("passkeys: a failed read shows the server's message with Retry, which reads again", async () => {
    let fail = true;
    const read = mock.method(api, "listPasskeys", async () => {
        if (fail) throw new Error("Passkeys are unavailable.");
        return [passkey] as Passkeys;
    });
    render(<PasskeyList />);
    const message = await screen.findByText("Passkeys are unavailable.", undefined, { timeout: SLOW });
    assert.equal(message.getAttribute("role"), "alert");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Laptop key"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
});

test("passkeys: an answer that is not a list is a failed read ('Failed to load passkeys'), with no crash", async () => {
    mock.method(api, "listPasskeys", async () => ({ detail: "unexpected" }) as unknown as Passkeys);
    render(<PasskeyList />);
    await waitFor(() => assert.equal(shown("Failed to load passkeys"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown("No passkeys registered yet."), false);
});
