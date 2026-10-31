import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { inboundApi } from "@/lib/inbound-api";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { AuthProvider } from "@/lib/auth-context";
import { InboundCampaignForm } from "./inbound-campaign-form";

const originals = { request: api.request, campaigns: dashboardApi.listCampaigns, numbers: inboundApi.availablePhoneNumbers, capabilities: inboundApi.getCapabilities, voices: aiOptionsApi.getVoices };
let client: QueryClient | undefined;
// JSDOM does not draw canvas; the Select measures its label width with one.
beforeEach(() => mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null));
afterEach(() => { cleanup(); client?.clear(); mock.restoreAll(); window.localStorage.clear(); api.request = originals.request; dashboardApi.listCampaigns = originals.campaigns; inboundApi.availablePhoneNumbers = originals.numbers; inboundApi.getCapabilities = originals.capabilities; aiOptionsApi.getVoices = originals.voices; });

test("real inbound form allows loaded IP trunks despite outbound timeout and rejects missing inbound proof", async () => {
    window.localStorage.setItem("talklee.auth.token", "synthetic-trunk-session");
    mock.method(api, "getMe", async () => ({ id: "synthetic-user", tenant_id: "synthetic-tenant", email: "trunk@example.invalid", role: "tenant_admin", minutes_state: "known", minutes_remaining: 1 }));
    dashboardApi.listCampaigns = async () => ({ campaigns: [] });
    inboundApi.availablePhoneNumbers = async () => [];
    inboundApi.getCapabilities = async () => ({ transfer_configuration_available: false }) as Awaited<ReturnType<typeof inboundApi.getCapabilities>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    api.request = async <T,>() => [
        { id: "ip-ready", trunk_name: "IP ready", direction: "both", is_active: true, auth_configured: false, runtime_ready: false, inbound_runtime_ready: true, runtime_status_detail: "No SIP response within 5s" },
        { id: "old-response", trunk_name: "Missing inbound proof", direction: "both", is_active: true, runtime_ready: true },
    ] as T;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<AuthProvider><QueryClientProvider client={client}><InboundCampaignForm mode="create" pending={false} canAssignNumber onSubmit={async () => {}} /></QueryClientProvider></AuthProvider>);
    // The shared Select is a combobox whose options render in a portal listbox
    // only while it is open (it once was a native <select>, which this test
    // still assumed: it searched for options inside the closed trigger).
    const select = screen.getByLabelText("Inbound SIP trunk") as HTMLButtonElement;
    await waitFor(() => assert.equal(select.disabled, false), { timeout: 10000 });
    fireEvent.click(select);
    const listbox = await screen.findByRole("listbox", { name: "Inbound SIP trunk" });
    const option = within(listbox).getByRole("option", { name: "IP ready · inbound ready" }) as HTMLButtonElement;
    assert.equal(option.disabled, false);
    assert.equal((within(listbox).getByRole("option", { name: "Missing inbound proof · inbound not ready" }) as HTMLButtonElement).disabled, true);
    fireEvent.click(option);
    await waitFor(() => assert.match(select.textContent ?? "", /IP ready · inbound ready/));
});
