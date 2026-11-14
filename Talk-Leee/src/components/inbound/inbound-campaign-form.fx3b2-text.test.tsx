/**
 * FX3b2 Task 1.3 — with no verified number the form says "phone number", not
 * DID. Same real-form mount as inbound-trunk-readiness.test.tsx.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { api } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { inboundApi } from "@/lib/inbound-api";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { AuthProvider } from "@/lib/auth-context";
import { InboundCampaignForm } from "./inbound-campaign-form";

const originals = { request: api.request, campaigns: dashboardApi.listCampaigns, numbers: inboundApi.availablePhoneNumbers, capabilities: inboundApi.getCapabilities, voices: aiOptionsApi.getVoices };
let client: QueryClient | undefined;
beforeEach(() => mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null));
afterEach(() => {
    cleanup(); client?.clear(); mock.restoreAll(); window.localStorage.clear();
    api.request = originals.request; dashboardApi.listCampaigns = originals.campaigns; inboundApi.availablePhoneNumbers = originals.numbers;
    inboundApi.getCapabilities = originals.capabilities; aiOptionsApi.getVoices = originals.voices;
});

test("FX3b2 1.3: with no verified number the form says 'phone number', not DID", async () => {
    window.localStorage.setItem("talklee.auth.token", "synthetic-session");
    mock.method(api, "getMe", async () => ({ id: "synthetic-user", tenant_id: "synthetic-tenant", email: "form@example.invalid", role: "tenant_admin", minutes_state: "known", minutes_remaining: 1 }));
    dashboardApi.listCampaigns = async () => ({ campaigns: [] });
    inboundApi.availablePhoneNumbers = async () => [];
    inboundApi.getCapabilities = async () => ({ transfer_configuration_available: false }) as Awaited<ReturnType<typeof inboundApi.getCapabilities>>;
    aiOptionsApi.getVoices = async () => ({ voices: [] });
    api.request = async <T,>() => [{ id: "ip-ready", trunk_name: "IP ready", direction: "both", is_active: true, auth_configured: false, runtime_ready: false, inbound_runtime_ready: true }] as T;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(<AuthProvider><QueryClientProvider client={client}><InboundCampaignForm mode="create" pending={false} canAssignNumber onSubmit={async () => {}} /></QueryClientProvider></AuthProvider>);
    await screen.findByText("No verified number is available. Ask an administrator to verify a phone number first.", undefined, { timeout: 10000 });
    assert.equal(/\bDID\b/.test(document.body.textContent ?? ""), false);
});
