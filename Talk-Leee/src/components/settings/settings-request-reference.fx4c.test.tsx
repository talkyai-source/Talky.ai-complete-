/**
 * FX4c Task A1 — the recording-policy and telephony-settings error states show
 * the failed request's reference ("Request reference: …").
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { RecordingPolicySection, RecordingPolicyView } from "@/components/settings/recording-policy-section";
import { TelephonyProvidersSection } from "@/components/settings/telephony-providers-section";
import { api } from "@/lib/api";
import { ApiClientError, setRequestIdentity, setTokenProvider } from "@/lib/http-client";

const SLOW = 15_000;
const originalFetch = globalThis.fetch;
const originalRequest = api.request;
const clients: QueryClient[] = [];
afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
    api.request = originalRequest;
    setTokenProvider(null);
    clients.splice(0).forEach((client) => client.clear());
});
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

test("recording policy: a failed read shows the server's request reference", async () => {
    setTokenProvider(() => null);
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async () => Response.json(
        { detail: "Recording policy store is unavailable." },
        { status: 503, headers: { "x-request-id": "req-fx4c-recording" } },
    )) as typeof fetch;
    render(<RecordingPolicySection canEdit />);
    await waitFor(() => assert.equal(shown("Recording policy store is unavailable."), true), { timeout: SLOW });
    assert.equal(shown("Request reference: req-fx4c-recording"), true);
});

test("recording policy view: no reference, no line", () => {
    render(<RecordingPolicyView policy={undefined} error="Recording policy store is unavailable." canEdit onSave={async () => undefined} />);
    assert.equal(shown("Recording policy store is unavailable."), true);
    assert.equal(shown(/Request reference/), false);
});

test("telephony settings: a failed read shows the server's request reference", async () => {
    api.request = (async () => {
        throw new ApiClientError({
            status: 500, code: "internal_error", message: "Provider store failed", requestId: "req-fx4c-telephony",
            url: "http://example.test/telephony/providers", method: "GET",
        });
    }) as typeof api.request;
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><TelephonyProvidersSection /></QueryClientProvider>);
    await waitFor(() => assert.equal(shown("Failed to load telephony settings"), true), { timeout: SLOW });
    assert.equal(shown("Request reference: req-fx4c-telephony"), true);
});
