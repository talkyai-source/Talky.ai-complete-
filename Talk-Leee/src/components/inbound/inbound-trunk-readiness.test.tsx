import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { inboundApi } from "@/lib/inbound-api";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { InboundCampaignForm } from "./inbound-campaign-form";

const originals = { request: api.request, campaigns: dashboardApi.listCampaigns, numbers: inboundApi.availablePhoneNumbers, capabilities: inboundApi.getCapabilities, voices: aiOptionsApi.getVoices };
let client: QueryClient | undefined;
afterEach(() => { cleanup(); client?.clear(); api.request = originals.request; dashboardApi.listCampaigns = originals.campaigns; inboundApi.availablePhoneNumbers = originals.numbers; inboundApi.getCapabilities = originals.capabilities; aiOptionsApi.getVoices = originals.voices; });

test("real inbound form allows loaded IP trunks despite outbound timeout and rejects missing inbound proof", async () => {
    dashboardApi.listCampaigns = async () => ({ campaigns: [] });
    inboundApi.availablePhoneNumbers = async () => [];
    inboundApi.getCapabilities = async () => ({ transfer_configuration_available: false }) as Awaited<ReturnType<typeof inboundApi.getCapabilities>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    api.request = async <T,>() => [
        { id: "ip-ready", trunk_name: "IP ready", direction: "both", is_active: true, auth_configured: false, runtime_ready: false, inbound_runtime_ready: true, runtime_status_detail: "No SIP response within 5s" },
        { id: "old-response", trunk_name: "Missing inbound proof", direction: "both", is_active: true, runtime_ready: true },
    ] as T;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<QueryClientProvider client={client}><InboundCampaignForm mode="create" pending={false} canAssignNumber onSubmit={async () => {}} /></QueryClientProvider>);
    const select = screen.getByLabelText("Inbound SIP trunk") as HTMLSelectElement;
    // Scope to this selector instead of repeatedly traversing every timezone
    // option while asynchronous inventory queries settle.
    const option = await within(select).findByRole("option", { name: "IP ready · inbound ready" }, { timeout: 10000 }) as HTMLOptionElement;
    assert.equal(option.disabled, false);
    assert.equal((within(select).getByRole("option", { name: "Missing inbound proof · inbound not ready" }) as HTMLOptionElement).disabled, true);
    await waitFor(() => assert.equal(select.disabled, false));
    fireEvent.change(select, { target: { value: "ip-ready" } });
    assert.equal(select.value, "ip-ready");
});
