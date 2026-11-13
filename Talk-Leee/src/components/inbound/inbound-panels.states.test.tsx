/**
 * FX2b Task 2.1, 2.3 — the Inbound detail panels: an answer missing its main
 * field is a failed read for that panel (shared ErrorState with Retry), never
 * a crash and never an empty panel; the rejected-calls panel loads and is
 * empty in the shared states. (Live calls and call issues: panel-states.test.tsx.)
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { RejectedInboundCallsPanel } from "@/components/campaigns/rejected-inbound-calls-panel";
import { api, sharedHttpClient, type RejectedInboundCallItem } from "@/lib/api";
import { inboundApi } from "@/lib/inbound-api";

const SLOW = 15_000;
afterEach(() => { cleanup(); mock.restoreAll(); });
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
type Rejected = Awaited<ReturnType<typeof api.listRejectedInboundCalls>>;
const item: RejectedInboundCallItem = {
    id: "r1", source: "pre_row", occurred_at: "2026-09-03T00:00:00Z", status: "denied", reason: "unknown_did",
    caller_ani: "+15550001111", called_did: "+15550002222",
};

test("rejected calls: loading is a status titled 'Loading rejected calls…'", async () => {
    let answer: (() => void) | null = null;
    mock.method(api, "listRejectedInboundCalls", () => new Promise<Rejected>((resolve) => {
        answer = () => resolve({ items: [], total: 0, page: 1, page_size: 25 } as unknown as Rejected);
    }));
    render(<RejectedInboundCallsPanel campaignId="c" />);
    const status = await screen.findByRole("status", undefined, { timeout: SLOW });
    assert.equal(within(status).queryByText("Loading rejected calls…") !== null, true);
    await waitFor(() => assert.equal(answer !== null, true), { timeout: SLOW });
    await act(async () => { answer!(); });
    await waitFor(() => assert.equal(shown("No rejected inbound calls"), true), { timeout: SLOW });
});

test("rejected calls: no rows shows 'No rejected inbound calls' in the shared EmptyState", async () => {
    mock.method(api, "listRejectedInboundCalls", async () => ({ items: [], total: 0, page: 1, page_size: 25 }) as unknown as Rejected);
    render(<RejectedInboundCallsPanel campaignId="c" />);
    const title = await screen.findByText("No rejected inbound calls", undefined, { timeout: SLOW });
    assert.equal(title.tagName, "H2", "the shared EmptyState title");
    assert.equal(shown("Denied and after-hours attempts will appear here."), true);
});

test("rejected calls: a failed read shows the server's message with Retry, which reads again", async () => {
    let fail = true;
    const read = mock.method(api, "listRejectedInboundCalls", async () => {
        if (fail) throw new Error("Rejected calls are unavailable.");
        return { items: [item], total: 1, page: 1, page_size: 25 } as unknown as Rejected;
    });
    render(<RejectedInboundCallsPanel campaignId="c" />);
    await waitFor(() => assert.equal(shown("Rejected calls are unavailable."), true), { timeout: SLOW });
    assert.equal(screen.getByText("Rejected calls are unavailable.").getAttribute("role"), "alert");
    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Unknown number"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), before + 1);
    assert.equal(shown("Rejected calls are unavailable."), false);
});

test("rejected calls: an answer without its list is a failed read, with no crash and no empty panel", async () => {
    mock.method(api, "listRejectedInboundCalls", async () => ({ total: 0 }) as unknown as Rejected);
    render(<RejectedInboundCallsPanel campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load rejected inbound calls"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown("No rejected inbound calls"), false);
});

test("knowledge: an answer without its tree and sources is a failed read, with no crash", async () => {
    mock.method(api, "getCampaignKnowledge", async () => ({ campaign_id: "c", knowledge_mode: "none" }) as unknown as Awaited<ReturnType<typeof api.getCampaignKnowledge>>);
    render(<KnowledgePanel campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load knowledge"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown("No knowledge yet"), false);
});

test("readiness: an answer without the readiness flag is a failed read, not 'not ready'", async () => {
    const request = mock.method(sharedHttpClient(), "request", async () => ({ checks: [] }));
    await assert.rejects(() => inboundApi.readiness("inb-1"));
    request.mock.mockImplementation(async () => ({ ready: false, checks: [], blockers: [] }));
    assert.equal((await inboundApi.readiness("inb-1")).ready, false);
});
