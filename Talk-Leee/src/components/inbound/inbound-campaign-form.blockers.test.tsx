/**
 * Live test, 14 Nov 2026: step 2 of "Create inbound campaign" ended in a
 * switched-off "Create inactive campaign" button with nothing beside it to say
 * why. The reasons were further up a long form. The form now says, next to the
 * button, what is still needed. Same real-form mount as
 * inbound-campaign-form.fx3b2-text.test.tsx.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { inboundApi } from "@/lib/inbound-api";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { AuthProvider } from "@/lib/auth-context";
import { InboundCampaignForm } from "./inbound-campaign-form";

const SLOW = 10_000;
const originals = { request: api.request, campaigns: dashboardApi.listCampaigns, numbers: inboundApi.availablePhoneNumbers, capabilities: inboundApi.getCapabilities, voices: aiOptionsApi.getVoices };
let client: QueryClient | undefined;
beforeEach(() => mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null));
afterEach(() => {
    cleanup(); client?.clear(); mock.restoreAll(); window.localStorage.clear();
    api.request = originals.request; dashboardApi.listCampaigns = originals.campaigns; inboundApi.availablePhoneNumbers = originals.numbers;
    inboundApi.getCapabilities = originals.capabilities; aiOptionsApi.getVoices = originals.voices;
});

const READY_TRUNK = { id: "ip-ready", trunk_name: "IP ready", direction: "both", is_active: true, auth_configured: false, runtime_ready: false, inbound_runtime_ready: true };
const NUMBER = { id: "n1", e164: "+442000000001", masked_number: "+44 •••• ••0001", label: "Main line", verification_status: "verified", assignment_status: "available", version: 1 };

function mount({ numbers, trunks, failTrunks = false }: { numbers: unknown[]; trunks: unknown[]; failTrunks?: boolean }) {
    window.localStorage.setItem("talklee.auth.token", "synthetic-session");
    mock.method(api, "getMe", async () => ({ id: "synthetic-user", tenant_id: "synthetic-tenant", email: "form@example.invalid", role: "tenant_admin", minutes_state: "known", minutes_remaining: 1 }));
    dashboardApi.listCampaigns = async () => ({ campaigns: [] });
    inboundApi.availablePhoneNumbers = (async () => numbers) as typeof inboundApi.availablePhoneNumbers;
    inboundApi.getCapabilities = async () => ({ transfer_configuration_available: false }) as Awaited<ReturnType<typeof inboundApi.getCapabilities>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    api.request = async <T,>() => { if (failTrunks) throw new Error("trunks unavailable"); return trunks as T; };
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<AuthProvider><QueryClientProvider client={client}><InboundCampaignForm mode="create" lockCampaign initialCampaignId="c1" pending={false} canAssignNumber onSubmit={async () => {}} /></QueryClientProvider></AuthProvider>);
}

async function blockers() {
    const note = await screen.findByText(/This campaign cannot be created yet\./, undefined, { timeout: SLOW });
    return note;
}

test("with no verified number and no connected trunk the form names both next to its button", async () => {
    mount({ numbers: [], trunks: [] });
    const note = await blockers();
    assert.match(note.textContent ?? "", /Still needed: a verified phone number \(none is available: ask an administrator to verify one\); an inbound trunk that is connected right now\./);
    const button = screen.getByRole("button", { name: /Create inactive campaign/ }) as HTMLButtonElement;
    assert.equal(button.disabled, true);
    assert.equal(button.getAttribute("aria-describedby"), note.id, "the reason is tied to the button for screen readers");
    assert.equal(note.getAttribute("role"), "status");
});

test("with a trunk but no number it names only the number", async () => {
    mount({ numbers: [], trunks: [READY_TRUNK] });
    const text = (await blockers()).textContent ?? "";
    assert.match(text, /a verified phone number/);
    assert.doesNotMatch(text, /inbound trunk/);
});

test("when the numbers and trunks cannot be loaded it says so instead of listing gaps", async () => {
    mount({ numbers: [], trunks: [], failTrunks: true });
    const text = (await blockers()).textContent ?? "";
    assert.match(text, /could not be loaded/);
    assert.doesNotMatch(text, /ask an administrator/);
});

test("with a number and a connected trunk there is no such note and the button works", async () => {
    mount({ numbers: [NUMBER], trunks: [READY_TRUNK] });
    const button = await screen.findByRole("button", { name: /Create inactive campaign/ }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    assert.equal(screen.queryByText(/cannot be created yet/), null);
    assert.equal(button.hasAttribute("aria-describedby"), false);
});
