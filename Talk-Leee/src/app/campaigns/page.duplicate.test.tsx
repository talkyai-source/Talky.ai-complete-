import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Campaign } from "@/lib/dashboard-api";

const SOURCE: Campaign = {
    id: "ddeeffaa-1234-4abc-8def-0123456789ab", name: "Summer sales", status: "running",
    direction: "outbound", system_prompt: "Saved prompt", voice_id: "saved-voice",
    max_concurrent_calls: 3, total_leads: 71, calls_completed: 37, calls_failed: 9,
    created_at: "2026-08-01T12:00:00Z",
};
const COPY: Campaign = { ...SOURCE, id: "abcdefff-1234-4abc-8def-0123456789ab",
    name: "Server-created copy", status: "draft", total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-11-06T12:00:00Z" };
let cache: Campaign[] = [];
let calls: string[] = [];
let notices: unknown[] = [];
let perform: () => Promise<{ campaign: Campaign }>;
const api = {
    duplicateCampaign: async (id: string) => { calls.push(id); return perform(); },
    pauseCampaign: async () => undefined, startCampaign: async () => undefined,
    deleteCampaign: async () => undefined,
};
const queryClient = {
    setQueryData: (_key: unknown, update: (previous: Campaign[]) => Campaign[]) => { cache = update(cache); },
    invalidateQueries: async () => undefined,
};
type Mutation = {
    mutationFn: (id: string) => Promise<unknown>;
    onSuccess?: (result: unknown, id: string) => unknown;
    onError?: (error: unknown) => unknown;
};
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("lib/dashboard-api")) return { dashboardApi: api };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    };
    if (id.includes("components/campaigns/campaign-performance-table")) return {
        CampaignPerformanceTable: ({ onDuplicate }: { onDuplicate: (id: string) => Promise<void> }) =>
            <button onClick={() => { void onDuplicate(SOURCE.id); }}>Duplicate</button>,
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
    if (id === "@tanstack/react-query") return { useQueryClient: () => queryClient };
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
    cache = [{ ...SOURCE }]; calls = []; notices = [];
    perform = async () => ({ campaign: COPY });
});
afterEach(cleanup);

test("Duplicate calls the API and inserts only the persisted server result", async () => {
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => assert.deepEqual(calls, [SOURCE.id]));
    await waitFor(() => assert.deepEqual(cache, [COPY, SOURCE]));
    assert.ok(cache.every((row) => !row.id.startsWith("camp-copy-")));
});

test("Duplicate creates no optimistic phantom while persistence is pending", async () => {
    let finish: (value: { campaign: Campaign }) => void = () => { throw new Error("Not pending"); };
    perform = () => new Promise((resolve) => { finish = resolve; });
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => assert.deepEqual(calls, [SOURCE.id]));
    assert.deepEqual(cache, [SOURCE]);
    finish({ campaign: COPY });
    await waitFor(() => assert.deepEqual(cache, [COPY, SOURCE]));
});

test("Duplicate refusal leaves the list intact and shows an error", async () => {
    perform = async () => { throw new Error("Campaign knowledge is still processing."); };
    render(<Page />);
    fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
    await waitFor(() => assert.deepEqual(calls, [SOURCE.id]));
    await waitFor(() => assert.ok(notices.some((value) => {
        const notice = value as { type?: string; message?: string };
        return notice.type === "error" && notice.message?.includes("knowledge is still processing");
    })));
    assert.deepEqual(cache, [SOURCE]);
});
