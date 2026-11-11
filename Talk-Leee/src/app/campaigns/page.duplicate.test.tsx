import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { CampaignDuplicateResult } from "@/lib/campaign-duplicate";
import type { Campaign } from "@/lib/dashboard-api";

// The list page does not copy a campaign itself (production-readiness 3.1): the
// table's own Duplicate flow (components/campaigns/campaign-duplicate over
// lib/campaign-duplicate) makes the copy and calls the page's onDuplicate only
// once it exists. These tests pin the page's side of that: it reads the list
// again, writes no row of its own and raises no notice of its own. The table's
// side (the real copy, its messages and refusals) is covered in page.test.tsx.
const SOURCE: Campaign = {
    id: "ddeeffaa-1234-4abc-8def-0123456789ab", name: "Summer sales", status: "running",
    direction: "outbound", system_prompt: "Saved prompt", voice_id: "saved-voice",
    max_concurrent_calls: 3, total_leads: 71, calls_completed: 37, calls_failed: 9,
    created_at: "2026-08-01T12:00:00Z",
};
const COPY: Campaign = { ...SOURCE, id: "abcdefff-1234-4abc-8def-0123456789ab",
    name: "Server-created copy", status: "draft", total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-11-06T12:00:00Z" };
const COPY_RESULT: CampaignDuplicateResult = { campaign: COPY, leadFieldsCopied: true, message: "Duplicated as “Server-created copy”." };
let cache: Campaign[] = [];
// What the old page-level route (dashboardApi.duplicateCampaign) received: the page must never call it.
let calls: string[] = [];
// The campaigns the table's copy routine ran for, and what the table was told when it was refused.
let copies: string[] = [];
let refusals: string[] = [];
let invalidated: unknown[] = [];
let notices: unknown[] = [];
let perform: () => Promise<CampaignDuplicateResult>;
const api = {
    duplicateCampaign: async (id: string) => { calls.push(id); return { campaign: COPY }; },
    pauseCampaign: async () => undefined, startCampaign: async () => undefined,
    deleteCampaign: async () => undefined,
};
const queryClient = {
    setQueryData: (_key: unknown, update: (previous: Campaign[]) => Campaign[]) => { cache = update(cache); },
    invalidateQueries: async (filters: { queryKey: unknown }) => { invalidated.push(filters.queryKey); },
};
type Mutation = {
    mutationFn: (id: string) => Promise<unknown>;
    onSuccess?: (result: unknown, id: string) => unknown;
    onError?: (error: unknown) => unknown;
};
// The table's own flow (useCampaignDuplicate): the shared routine makes the copy
// and the page is told only once it exists; a refused copy never reaches the page.
async function copyThenReport(onCreated: (result: CampaignDuplicateResult) => Promise<void>) {
    copies.push(SOURCE.id);
    let result: CampaignDuplicateResult;
    try {
        result = await perform();
    } catch (error) {
        refusals.push(error instanceof Error ? error.message : String(error));
        return;
    }
    await onCreated(result);
}
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    // Every other export (e.g. the Start dialog's isMinutesExhausted) stays real.
    if (id.includes("lib/dashboard-api")) return { ...(originalLoad.call(this, id, ...args) as object), dashboardApi: api };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    };
    if (id.includes("components/campaigns/campaign-performance-table")) return {
        CampaignPerformanceTable: ({ onDuplicate }: { onDuplicate: (result: CampaignDuplicateResult) => Promise<void> }) =>
            <button onClick={() => { void copyThenReport(onDuplicate); }}>Duplicate</button>,
    };
    if (id.includes("components/campaigns/event-stream")) return { EventStream: nothing };
    if (id.includes("components/campaigns/alert-timeline")) return { AlertTimeline: nothing };
    if (id.includes("components/campaigns/command-bar")) return { CommandBar: nothing };
    if (id.includes("lib/api-hooks")) return {
        queryKeys: { campaigns: () => ["campaigns"] },
        useOutboundCampaigns: () => ({ data: [SOURCE], isLoading: false, isError: false }),
    };
    // Campaign actions follow effective permissions (QA F1-21); unknown offers all.
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false }) };
    // The page's Start dialog reads calling readiness with useQueries; no dialog opens here, so it is idle.
    if (id === "@tanstack/react-query") return { useQueryClient: () => queryClient, useQueries: () => [] };
    if (id.includes("lib/notifications-client")) return { useNotificationsActions: () => ({
        create: (notice: unknown) => { notices.push(notice); },
    }) };
    if (id.includes("lib/notification-mutations")) return {
        useNotificationMutation: (options: Mutation) => ({
            mutateAsync: async (id: string) => {
                try {
                    const result = await options.mutationFn(id);
                    await options.onSuccess?.(result, id);
                    return result;
                } catch (error) {
                    await options.onError?.(error);
                    throw error;
                }
            },
        }),
    };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped view boundaries, actual page action runs
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

beforeEach(() => {
    cache = [{ ...SOURCE }]; calls = []; copies = []; refusals = []; invalidated = []; notices = [];
    perform = async () => COPY_RESULT;
});
afterEach(cleanup);

test("once the table reports the copy, the page reads the list again and inserts no row itself", async () => {
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => assert.deepEqual(invalidated, [["campaigns"]]));
    assert.deepEqual(copies, [SOURCE.id]);
    assert.deepEqual(calls, [], "the page makes no copy of its own");
    assert.deepEqual(cache, [SOURCE], "the copy comes with the re-read list, never from a cache write");
});

test("Duplicate creates no optimistic phantom: the page does nothing while the copy is pending", async () => {
    let finish: (value: CampaignDuplicateResult) => void = () => { throw new Error("Not pending"); };
    perform = () => new Promise((resolve) => { finish = resolve; });
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => assert.deepEqual(copies, [SOURCE.id]));
    assert.deepEqual(invalidated, []);
    assert.deepEqual(cache, [SOURCE]);
    finish(COPY_RESULT);
    await waitFor(() => assert.deepEqual(invalidated, [["campaigns"]]));
    assert.deepEqual(cache, [SOURCE]);
    assert.deepEqual(calls, []);
});

test("a refused copy never reaches the page: the list stays intact and the page raises no notice of its own", async () => {
    perform = async () => { throw new Error("Campaign knowledge is still processing."); };
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => assert.deepEqual(refusals, ["Campaign knowledge is still processing."]));
    assert.deepEqual(invalidated, []);
    assert.deepEqual(cache, [SOURCE]);
    assert.deepEqual(notices, [], "the refusal is shown by the table's details modal, not by the page");
    assert.deepEqual(calls, []);
});
