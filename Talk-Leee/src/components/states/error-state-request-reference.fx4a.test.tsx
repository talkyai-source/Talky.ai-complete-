/**
 * FX4a Task 1 — every shared ErrorState shows the failed request's reference
 * ("Request reference: …") when that request has one.
 *
 * The first test is a guard over the source: each <ErrorState> use passes
 * `requestId`, apart from the listed uses that have no failed request, the
 * pages to be hidden later and the files held for the other developer's open
 * branches. The other tests render the components and read the reference on
 * screen.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import DeviceList from "@/components/auth/device-list";
import PasskeyList from "@/components/auth/passkey-list";
import { CallIssuesBanner } from "@/components/calls/call-issues-banner";
import { CallLoadError, CallsLoadError, SummaryPreview } from "@/components/calls/call-panels";
import { CallIssuesPanel } from "@/components/campaigns/call-issues-panel";
import { ContactLists } from "@/components/campaigns/contact-lists";
import { KnowledgePanel } from "@/components/campaigns/knowledge-panel";
import { RejectedInboundCallsPanel } from "@/components/campaigns/rejected-inbound-calls-panel";
import { ScriptCard } from "@/components/campaigns/script-card";
import { DialerInsights } from "@/components/dashboard/dialer-insights";
import { InboundErrorState } from "@/components/inbound/inbound-page-state";
import { api } from "@/lib/api";
import { dashboardApi } from "@/lib/dashboard-api";
import { extendedApi } from "@/lib/extended-api";

const SLOW = 15_000;
const REFERENCE = "req-fx4a-7781";
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const referenceShown = () => shown(`Request reference: ${REFERENCE}`);
const failed = (message = "The server refused.") => Object.assign(new Error(message), { requestId: REFERENCE });

const clients: QueryClient[] = [];
afterEach(() => { cleanup(); mock.restoreAll(); clients.splice(0).forEach((client) => client.clear()); });

// ───────── guard over the source ─────────

// Not edited in FX4a: the other developer's open branches change these files.
const HELD_FOR_BRANCHES = new Set([
    "app/contacts/page.tsx",
    "app/settings/page.tsx",
    "components/settings/recording-policy-section.tsx",
    "components/settings/telephony-providers-section.tsx",
]);
// To be hidden later.
const HIDDEN_LATER = new Set(["app/meetings/page.tsx", "app/reminders/page.tsx"]);
// Uses with no failed request behind them: a refused permission that was read
// successfully, and "No ticket selected".
const NO_FAILED_REQUEST: Record<string, number> = {
    "app/recordings/page.tsx": 1,
    "app/support/tickets/[id]/page.tsx": 1,
    "components/inbound/inbound-page-state.tsx": 1,
};

function errorStateUses(source: string) {
    const uses: string[] = [];
    let from = 0;
    for (;;) {
        const start = source.indexOf("<ErrorState", from);
        if (start === -1) return uses;
        let end = start;
        let depth = 0;
        for (; end < source.length; end += 1) {
            const char = source[end];
            if (char === "{") depth += 1;
            else if (char === "}") depth -= 1;
            else if (char === ">" && depth === 0 && source[end - 1] !== "=") break;
        }
        uses.push(source.slice(start, end + 1));
        from = end;
    }
}

test("every ErrorState use passes the request reference, apart from the listed ones", () => {
    const root = path.join(process.cwd(), "src");
    const missing: Record<string, number> = {};
    let total = 0;
    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) { walk(full); continue; }
            if (!full.endsWith(".tsx") || full.includes(".test.") || full.includes(".stories.")) continue;
            const file = path.relative(root, full).split(path.sep).join("/");
            if (HELD_FOR_BRANCHES.has(file) || HIDDEN_LATER.has(file)) continue;
            for (const use of errorStateUses(fs.readFileSync(full, "utf8"))) {
                total += 1;
                if (!use.includes("requestId")) missing[file] = (missing[file] ?? 0) + 1;
            }
        }
    };
    walk(root);
    assert.equal(total > 40, true, `the scan found the ErrorState uses (${total})`);
    assert.deepEqual(missing, NO_FAILED_REQUEST);
});

// ───────── the wrappers pass the reference on ─────────

test("CallLoadError, CallsLoadError and SummaryPreview show the reference they are given", () => {
    render(<CallLoadError message="Failed to load call details" requestId={REFERENCE} onRetry={() => {}} />);
    assert.equal(referenceShown(), true, "call detail");
    cleanup();
    render(<CallsLoadError message="Failed to load calls" requestId={REFERENCE} onRetry={() => {}} />);
    assert.equal(referenceShown(), true, "call history");
    cleanup();
    render(<SummaryPreview isLoading={false} isError requestId={REFERENCE} />);
    assert.equal(referenceShown(), true, "summary preview");
    cleanup();
    render(<CallsLoadError message="Failed to load calls" onRetry={() => {}} />);
    assert.equal(shown(/Request reference/), false, "no reference, no line");
});

test("InboundErrorState shows the reference on a failed read", () => {
    render(<InboundErrorState message="The campaign list could not be loaded." requestId={REFERENCE} onRetry={() => {}} />);
    assert.equal(referenceShown(), true);
});

// ───────── components that keep the failure in their own state ─────────

test("contact lists: the failed read's reference is shown", async () => {
    mock.method(api, "request", async () => ({ ready: true }));
    mock.method(dashboardApi, "listContactLists", async () => { throw failed(); });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><ContactLists campaignId="campaign-a" /></QueryClientProvider>);
    await waitFor(() => assert.equal(shown("Failed to load contact lists"), true), { timeout: SLOW });
    assert.equal(referenceShown(), true);
});

test("knowledge: the failed read's reference is shown", async () => {
    mock.method(api, "getCampaignKnowledge", async () => { throw failed(); });
    render(<KnowledgePanel campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load knowledge"), true), { timeout: SLOW });
    assert.equal(referenceShown(), true);
});

test("script: the failed read's reference is shown", async () => {
    mock.method(extendedApi, "getCampaignCallsWithTranscripts", async () => { throw failed(); });
    render(<ScriptCard campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load transcripts"), true), { timeout: SLOW });
    assert.equal(referenceShown(), true);
});

test("call issues panel: the failed read's reference is shown", async () => {
    mock.method(api, "listCallIssues", async () => { throw failed(); });
    render(<CallIssuesPanel campaignId="c" />);
    await waitFor(() => assert.equal(shown("Failed to load call issues"), true), { timeout: SLOW });
    assert.equal(referenceShown(), true);
});

test("rejected inbound calls: the failed read's reference is shown", async () => {
    mock.method(api, "listRejectedInboundCalls", async () => { throw failed("Rejections could not be read."); });
    render(<RejectedInboundCallsPanel campaignId="campaign-1" />);
    await waitFor(() => assert.equal(shown("Rejections could not be read."), true), { timeout: SLOW });
    assert.equal(referenceShown(), true);
});

test("devices: the failed read's reference is shown", async () => {
    mock.method(api, "getActiveSessions", async () => { throw failed("Sessions could not be read."); });
    render(<DeviceList token="" />);
    await waitFor(() => assert.equal(shown("Sessions could not be read."), true), { timeout: SLOW });
    assert.equal(referenceShown(), true);
});

test("passkeys: the failed read's reference is shown", async () => {
    mock.method(api, "listPasskeys", async () => { throw failed("Passkeys could not be read."); });
    render(<PasskeyList />);
    await waitFor(() => assert.equal(shown("Passkeys could not be read."), true), { timeout: SLOW });
    assert.equal(referenceShown(), true);
});

test("dialer insights and the call issues banner: the failed read's reference is shown", async () => {
    mock.method(extendedApi, "getBestTimeToCall", async () => { throw failed("Insights could not be read."); });
    mock.method(extendedApi, "getRetryEffectiveness", async () => { throw failed("Insights could not be read."); });
    render(<DialerInsights />);
    await waitFor(() => assert.equal(referenceShown(), true), { timeout: SLOW });
    cleanup();
    mock.method(extendedApi, "getRecentCallIssues", async () => { throw failed("Issues could not be read."); });
    render(<CallIssuesBanner />);
    await waitFor(() => assert.equal(referenceShown(), true), { timeout: SLOW });
});
