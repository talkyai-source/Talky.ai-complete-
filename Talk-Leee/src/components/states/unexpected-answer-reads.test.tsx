/**
 * FX2b3 Task 3.2 — the reads behind the two "Application error" pages: an
 * answer without its main field is a failed read (the request rejects), and
 * the call page's other-reviews panel shows a failed read with Retry.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { z } from "zod";

import { ConversationReviewPanel } from "@/components/calls/conversation-review-panel";
import { VoiceProviderPicker } from "@/components/campaigns/voice-provider-picker";
import { aiOptionsApi, type AIProviderConfig } from "@/lib/ai-options-api";
import { sharedHttpClient } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";
import { leadDetailsApi } from "@/lib/lead-details-api";
import { inboundQueryKeys } from "@/lib/queries/inbound-queries";

const SLOW = 15_000;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); mock.restoreAll(); clients.splice(0).forEach((client) => client.clear()); });
const answer = (body: unknown) => mock.method(sharedHttpClient(), "request", async () => body);

test("contact fields: an answer without its field list is a failed read (the campaign wizard no longer crashes on it)", async () => {
    const request = answer({});
    await assert.rejects(() => leadDetailsApi.fieldSpec());
    request.mock.mockImplementation(async () => ({ fields: [], csv_template_headers: [] }));
    assert.deepEqual((await leadDetailsApi.fieldSpec()).fields, []);
});

test("call: an answer without the call is a failed read", async () => {
    const request = answer({});
    await assert.rejects(() => dashboardApi.getCall("call-1"));
    request.mock.mockImplementation(async () => ({ id: "call-1", timestamp: "2026-01-01T00:00:00Z", to_number: "+447400123456", status: "completed" }));
    assert.equal((await dashboardApi.getCall("call-1")).id, "call-1");
});

test("call reviews: an answer that is not a list is a failed read", async () => {
    const request = answer({});
    await assert.rejects(() => extendedApi.listCallReviews("call-1"));
    request.mock.mockImplementation(async () => []);
    assert.deepEqual(await extendedApi.listCallReviews("call-1"), []);
});

test("call reviews panel: a failed read of the other reviews shows the ErrorState with Retry, which reads again; no crash", async () => {
    mock.method(extendedApi, "getReviewOptions", async () => ({
        tags: ["good_conversation"], rewards_enabled: false, points_per_review: 0, daily_cap: 0, bare_rating_earns_reward: false,
    }));
    mock.method(extendedApi, "getMyReview", async () => null);
    let fail = true;
    const list = mock.method(extendedApi, "listCallReviews", async () => {
        if (fail) throw new Error();
        return [];
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    client.setQueryData(inboundQueryKeys.permissions, { permissions: ["calls:read"] });
    render(<QueryClientProvider client={client}><ConversationReviewPanel callId="call-1" /></QueryClientProvider>);
    const message = await screen.findByText("Please retry.", undefined, { timeout: SLOW });
    assert.equal(message.getAttribute("role"), "alert");
    fail = false;
    const before = list.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(screen.queryByText("Please retry.") === null, true), { timeout: SLOW });
    assert.equal(list.mock.callCount(), before + 1);
});

test("voice picker: a voices answer the app could not read shows 'Voices could not be loaded. Please retry.', never the validation dump", async () => {
    const parsed = z.object({ voices: z.array(z.string()) }).safeParse({});
    assert.equal(parsed.success, false);
    mock.method(aiOptionsApi, "getVoices", async () => { throw parsed.error; });
    mock.method(aiOptionsApi, "getConfig", async () => ({ tts_provider: "cartesia" }) as AIProviderConfig);
    render(<VoiceProviderPicker personaType="lead_gen" voiceId="" onVoiceChange={() => {}} />);
    const sentence = await screen.findByText("Voices could not be loaded. Please retry.", undefined, { timeout: SLOW });
    assert.equal(sentence.closest('[role="alert"]') !== null, true);
    assert.equal(screen.queryByText(/invalid_type|"expected"/) === null, true);
    assert.equal(screen.queryByRole("button", { name: "Retry voices" }) !== null, true);
});
