/**
 * FX2a Task 1.2, 1.5, 1.6, 3.1 — the campaign sub-panels use the shared
 * loading / error states with each panel's own "Failed to load …" sentence
 * and Retry; an unexpected answer is a failed read (no crash, no empty list);
 * the readiness notice says "Retry" only after a failed read; "Call this list"
 * asks in the app's confirm dialog.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { CallIssuesPanel } from "@/components/campaigns/call-issues-panel";
import { CampaignStartControl } from "@/components/campaigns/campaign-readiness";
import { ContactLists } from "@/components/campaigns/contact-lists";
import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { LiveCallsPanel } from "@/components/campaigns/live-calls-panel";
import { ScriptCard } from "@/components/campaigns/script-card";
import { api } from "@/lib/api";
import { dashboardApi, type ContactList } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";

const SLOW = 15_000;
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const clients: QueryClient[] = [];
const originalConfirm = window.confirm;
let confirms = 0;

beforeEach(() => {
    confirms = 0;
    window.confirm = () => { confirms += 1; return true; };
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    clients.splice(0).forEach((client) => client.clear());
    window.confirm = originalConfirm;
});

function mount(node: React.ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
    return client;
}

const list: ContactList = { id: "list-1", name: "Priority leads", source: "csv", is_active: false, contact_count: 3, created_at: "2026-09-03T00:00:00Z" };
const CALL_SENTENCE = /Call all eligible contacts in “Priority leads” now\?\s+This places REAL outbound calls immediately — just like starting the campaign\./;

// ───────── contact lists ─────────

test("contact lists: loading is a status; a failed read shows 'Failed to load contact lists' with Retry, which reads again", async () => {
    mock.method(api, "request", async () => ({ ready: true }));
    let fail = true;
    let answer: (() => void) | null = null;
    const read = mock.method(dashboardApi, "listContactLists", () => new Promise<ContactList[]>((resolve, reject) => {
        const settle = fail ? () => reject(new Error("upstream said: boom")) : () => resolve([list]);
        if (read.mock.callCount() === 0) answer = settle; else settle();
    }));
    mount(<ContactLists campaignId="campaign-a" />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading contact lists…") !== null, true);
    await act(async () => { answer!(); });

    await waitFor(() => assert.equal(shown("Failed to load contact lists"), true), { timeout: SLOW });
    assert.equal(screen.getByText("Failed to load contact lists").getAttribute("role"), "alert");
    assert.equal(shown("upstream said: boom"), false, "the panel's own sentence is the message");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Priority leads"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
});

test("contact lists: an answer that is not a list is a failed read, not an empty panel", async () => {
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(dashboardApi, "listContactLists", async () => ({ detail: "unexpected" }) as unknown as ContactList[]);
    mount(<ContactLists campaignId="campaign-a" />);
    await waitFor(() => assert.equal(shown("Failed to load contact lists"), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
});

async function openCallDialog() {
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(dashboardApi, "listContactLists", async () => [list]);
    mount(<ContactLists campaignId="campaign-a" />);
    const button = await screen.findByRole("button", { name: "Call this list" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(button.disabled, false), { timeout: SLOW });
    fireEvent.click(button);
    return within(await screen.findByRole("dialog"));
}

test("Call this list asks in the app's confirm dialog; Cancel places no call", async () => {
    const call = mock.method(dashboardApi, "callContactList", async () => { throw new Error("must not call"); });
    const dialog = await openCallDialog();
    assert.equal(dialog.queryByText(CALL_SENTENCE) !== null, true, "the existing confirm sentence is the body");
    assert.equal(dialog.queryAllByText("Call this list").length, 2, "title and confirm button carry the opener's label");
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(call.mock.callCount(), 0);
    assert.equal(confirms, 0, "no browser confirm()");
});

test("Call this list: confirming in the dialog places the call once", async () => {
    const call = mock.method(dashboardApi, "callContactList", async () => ({
        list_id: list.id, is_active: true, eligible_count: 3, jobs_enqueued: 3, started: true, message: "Calls queued.",
    }));
    const dialog = await openCallDialog();
    fireEvent.click(dialog.getByRole("button", { name: "Call this list" }));
    await waitFor(() => assert.equal(call.mock.callCount(), 1), { timeout: SLOW });
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(confirms, 0, "no browser confirm()");
});

// ───────── knowledge ─────────

test("knowledge: loading is a status; a failed read shows 'Failed to load knowledge' with Retry and never 'No knowledge yet'", async () => {
    let fail = true;
    let answer: (() => void) | null = null;
    const read = mock.method(api, "getCampaignKnowledge", () => new Promise((resolve, reject) => {
        const settle = fail
            ? () => reject(new Error("knowledge store down"))
            : () => resolve({ campaign_id: "c", knowledge_mode: "none", sources: [], tree: [] });
        if (read.mock.callCount() === 0) answer = settle; else settle();
    }) as ReturnType<typeof api.getCampaignKnowledge>);
    render(<KnowledgePanel campaignId="c" />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading knowledge…") !== null, true);
    await act(async () => { answer!(); });

    await waitFor(() => assert.equal(shown("Failed to load knowledge"), true), { timeout: SLOW });
    assert.equal(screen.getByText("Failed to load knowledge").getAttribute("role"), "alert");
    assert.equal(shown("No knowledge yet"), false, "a failed read is not an empty knowledge base");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("No knowledge yet"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
    assert.equal(shown("Failed to load knowledge"), false);
});

// ───────── script ─────────

test("script: loading is a status; a failed read shows 'Failed to load transcripts' with Retry and never 'No calls yet'", async () => {
    let fail = true;
    let answer: (() => void) | null = null;
    const read = mock.method(extendedApi, "getCampaignCallsWithTranscripts", () => new Promise((resolve, reject) => {
        const settle = fail
            ? () => reject(new Error("transcripts down"))
            : () => resolve({ items: [], total: 0, page: 1, page_size: 20 });
        if (read.mock.callCount() === 0) answer = settle; else settle();
    }) as ReturnType<typeof extendedApi.getCampaignCallsWithTranscripts>);
    render(<ScriptCard campaignId="c" />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading") !== null, true);
    await act(async () => { answer!(); });

    await waitFor(() => assert.equal(shown("Failed to load transcripts"), true), { timeout: SLOW });
    assert.equal(screen.getByText("Failed to load transcripts").getAttribute("role"), "alert");
    assert.equal(shown(/No calls yet/), false, "a failed read is not an empty script");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown(/No calls yet/), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
});

test("script: an answer without an items list is a failed read, with no crash", async () => {
    mock.method(extendedApi, "getCampaignCallsWithTranscripts", async () => ({ total: 3 }) as unknown as Awaited<ReturnType<typeof extendedApi.getCampaignCallsWithTranscripts>>);
    render(<ScriptCard campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load transcripts"), true), { timeout: SLOW });
    assert.equal(shown(/No calls yet/), false);
});

// ───────── call issues ─────────

test("call issues: a failed read shows 'Failed to load call issues' with Retry, which reads again", async () => {
    let fail = true;
    const read = mock.method(api, "listCallIssues", async () => {
        if (fail) throw new Error("issues feed down");
        return { items: [] };
    });
    render(<CallIssuesPanel campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load call issues"), true), { timeout: SLOW });
    assert.equal(screen.getByText("Failed to load call issues").getAttribute("role"), "alert");
    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("Failed to load call issues"), false), { timeout: SLOW });
    assert.equal(read.mock.callCount() > before, true, "Retry sent the request again");
});

test("call issues: an answer without an items list is a failed read, with no crash", async () => {
    mock.method(api, "listCallIssues", async () => ({}) as unknown as Awaited<ReturnType<typeof api.listCallIssues>>);
    render(<CallIssuesPanel campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load call issues"), true), { timeout: SLOW });
});

// ───────── live calls ─────────

test("live calls: a /calls/live answer without an items list is a failed read, with no crash", async () => {
    mock.method(api, "listLiveCalls", async () => ({ server_time: "2026-11-10T00:00:00Z" }) as unknown as Awaited<ReturnType<typeof api.listLiveCalls>>);
    mount(<LiveCallsPanel campaignId="c" title="Live calls" />);
    await waitFor(() => assert.equal(shown("Failed to load live calls"), true), { timeout: SLOW });
    assert.equal(shown("Live calls"), true, "the panel is still rendered");
});

// ───────── readiness ─────────

test("readiness: a successful 'not ready' answer keeps 'Check again'", async () => {
    mock.method(api, "request", async () => ({ ready: false, reason: "Selected SIP trunk is not ready" }));
    mount(<CampaignStartControl campaignIds={["campaign-a"]} onStart={async () => {}} label="Resume" />);
    await screen.findByText(/Selected SIP trunk is not ready/, undefined, { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Check again" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true);
});

test("readiness: after a failed read the button reads 'Retry' and checks again", async () => {
    let fail = true;
    const read = mock.method(api, "request", async () => {
        if (fail) throw new Error("Unavailable");
        return { ready: true };
    });
    mount(<CampaignStartControl campaignIds={["campaign-a"]} onStart={async () => {}} label="Resume" />);
    await screen.findByText(/Calling readiness could not be checked/, undefined, { timeout: SLOW });
    assert.equal(screen.queryByRole("button", { name: "Check again" }) === null, true);
    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal((screen.getByRole("button", { name: "Resume" }) as HTMLButtonElement).disabled, false), { timeout: SLOW });
    assert.equal(read.mock.callCount() > before, true);
});
