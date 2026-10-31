import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { TelephonyProvidersSection } from "./telephony-providers-section";

const request = api.request;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); api.request = request; notificationsStore.setIdentity(null); });

function response(active = "none", saved = true, allowed = false) {
    const availability = { activation_allowed: allowed, qualification_only: allowed, reason_code: allowed ? null : "cloud_telephony_unavailable", reason: allowed ? "Nonproduction qualification only." : "Cloud calling is unavailable in production. Credentials remain saved." };
    return { active, availability: { twilio: availability, vonage: availability }, providers: saved ? ["twilio", "vonage"].map(provider => ({ provider, status: "active", has_credentials: true, last_test_result: { ok: true, check_scope: provider === "twilio" ? "provider_account" : "sdk_initialization" } })) : [] };
}

function mount(data: unknown, mutation: (options: Parameters<typeof api.request>[0]) => Promise<unknown> = async () => ({})) {
    const writes: Parameters<typeof api.request>[0][] = [];
    notificationsStore.setIdentity({ tenantId: "cloud-ui", userId: "owner" });
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        if (options.method && options.method !== "GET") { writes.push(options); return await mutation(options) as T; }
        if (options.path === "/telephony/providers") return data as T;
        if (options.path.endsWith("/pool-assignment")) return {} as T;
        return [] as T;
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><TelephonyProvidersSection /></QueryClientProvider>);
    return writes;
}

function card(name: string) { return screen.getByRole("heading", { name }).closest("div.rounded-xl") as HTMLElement; }

test("successful credential checks cannot enable production cloud activation", async () => {
    const writes = mount(response());
    await screen.findByRole("heading", { name: "Twilio" });
    for (const name of ["Twilio", "Vonage"]) {
        const button = within(card(name)).getByRole("button", { name: "Make active" }) as HTMLButtonElement;
        assert.equal(button.disabled, true);
        fireEvent.click(button);
    }
    assert.equal(writes.length, 0);
    assert.equal(screen.getAllByText(/Cloud calling is unavailable/).length, 2);
});

test("legacy saved cloud selection needs attention without changing the pointer or credentials", async () => {
    const writes = mount(response("twilio"));
    await screen.findByText("Twilio selection needs attention");
    assert.equal(screen.queryByText("Twilio is the active provider"), null);
    assert.equal(screen.getAllByText(/Credentials are saved/).length, 2);
    assert.equal(writes.length, 0);
});

test("unsaved provider cards still show backend unavailability", async () => {
    mount(response("none", false));
    await screen.findByRole("heading", { name: "Twilio" });
    assert.equal(screen.getAllByText(/Cloud calling is unavailable/).length, 2);
});

test("legacy response without availability never infers calling availability", async () => {
    const data = response();
    const legacy = { active: data.active, providers: data.providers };
    mount(legacy);
    await screen.findByRole("heading", { name: "Twilio" });
    for (const button of screen.getAllByRole("button", { name: "Make active" })) assert.equal((button as HTMLButtonElement).disabled, true);
    assert.equal(screen.getAllByText(/Availability could not be verified/).length, 2);
});

test("malformed availability cannot turn a false string into an enabled action", async () => {
    const data = response();
    const writes = mount({ ...data, availability: { ...data.availability, twilio: { ...data.availability.twilio, activation_allowed: "false" } } });
    await screen.findByText("Failed to load telephony settings");
    assert.equal(screen.queryByRole("button", { name: "Make active" }), null);
    assert.equal(writes.length, 0);
});

test("older successful check remains unspecified and never becomes route proof", async () => {
    const data = response();
    mount({ ...data, providers: data.providers.map(row => ({ ...row, last_test_result: { ok: true } })) });
    await screen.findByRole("heading", { name: "Twilio" });
    assert.equal(screen.getAllByText(/Previous check passed; scope not recorded/).length, 2);
});

test("a failed account timeout with null HTTP status does not hide saved settings", async () => {
    const data = response();
    mount({ ...data, providers: data.providers.map(row => ({ ...row, last_test_result: { ok: false, error: "Synthetic account check timed out", status_code: null } })) });
    await screen.findByRole("heading", { name: "Twilio" });
    assert.equal(screen.getAllByText(/Synthetic account check timed out/).length, 2);
    assert.equal(screen.queryByText("Failed to load telephony settings"), null);
});

test("account and local SDK checks are distinguished from call readiness", async () => {
    mount(response());
    await screen.findByRole("heading", { name: "Twilio" });
    assert.ok(within(card("Twilio")).getByText(/Provider account verified/));
    assert.ok(within(card("Vonage")).getByText(/Local configuration checked; provider was not contacted/));
    assert.equal(screen.queryByText(/Last test OK/), null);
    assert.equal(screen.getAllByText(/This check does not verify call readiness/).length, 2);
});

test("explicit nonproduction activation is qualification only and does not promise routing", async () => {
    const writes = mount(response("none", true, true), async () => ({ active: "twilio" }));
    await screen.findByRole("heading", { name: "Twilio" });
    fireEvent.click(within(card("Twilio")).getByRole("button", { name: "Make active" }));
    await waitFor(() => assert.equal(writes.length, 1));
    await waitFor(() => assert.ok(notificationsStore.getSnapshot().notifications.some(n => /qualification/.test(n.message || ""))));
    assert.equal(notificationsStore.getSnapshot().notifications.some(n => /calls will route/.test(n.message || "")), false);
});
