import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { createElement } from "react";
import { cleanup, waitFor } from "@testing-library/react";

import { useAccountAlertQuery } from "@/lib/account-alert-query";
import { useAlerts } from "@/lib/alerts-api";
import { backendApi } from "@/lib/backend-api";
import { ApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

// While the terms dialog is up the server refuses data reads with 403 `legal_acceptance_required`.
// The two alert polls must stop on it (the page is loaded again after acceptance), as they do on `forbidden`.
ensureDom();
const A = { tenantId: "tenant-a", userId: "user-a" };
const originalAlerts = backendApi.alerts.list;

function refusal(code: string, status: number) {
    return new ApiClientError({ code, message: "refused", status, url: "/x", method: "GET" });
}
type PollOptions = { refetchInterval?: unknown; refetchOnWindowFocus?: unknown };
function pollOptions(qc: ReturnType<typeof renderWithQueryClient>["qc"]) {
    const query = qc.getQueryCache().getAll()[0];
    return { query, options: query.observers[0].options as PollOptions };
}
function intervalOf(qc: ReturnType<typeof renderWithQueryClient>["qc"]) {
    const { query, options } = pollOptions(qc);
    return (options.refetchInterval as (q: unknown) => unknown)(query);
}

beforeEach(() => { notificationsStore.setIdentity(null); notificationsStore.setIdentity(A); });
afterEach(() => { cleanup(); notificationsStore.setIdentity(null); backendApi.alerts.list = originalAlerts; });

function Account({ fetcher }: { fetcher: () => Promise<number> }) {
    const { scopeKey, generation } = notificationsStore.getSnapshot();
    useAccountAlertQuery("poll-test", scopeKey!, generation, fetcher);
    return null;
}
function Alerts() {
    useAlerts({ severity: new Set(), type: new Set(), status: new Set() });
    return null;
}

for (const [code, status] of [["legal_acceptance_required", 403], ["forbidden", 403]] as const) {
    test(`account alert poll stops on ${code}: no refetch interval, no retry`, async () => {
        let calls = 0;
        const world = renderWithQueryClient(createElement(Account, { fetcher: async () => { calls += 1; throw refusal(code, status); } }));
        await waitFor(() => assert.equal(world.qc.getQueryCache().getAll()[0].state.status, "error"));
        assert.equal(intervalOf(world.qc), false);
        await new Promise(resolve => setTimeout(resolve, 1300));
        assert.equal(calls, 1, "no retry");
    });
}

test("account alert poll still retries an ordinary 500 and keeps its interval", async () => {
    let calls = 0;
    const world = renderWithQueryClient(createElement(Account, { fetcher: async () => { calls += 1; throw refusal("server_error", 500); } }));
    await waitFor(() => assert.ok(calls >= 2), { timeout: 4000 });
    await waitFor(() => assert.equal(world.qc.getQueryCache().getAll()[0].state.status, "error"), { timeout: 4000 });
    assert.equal(intervalOf(world.qc), 10_000);
});

test("alerts poll stops on legal_acceptance_required: no refetch interval, no retry", async () => {
    let calls = 0;
    backendApi.alerts.list = async () => { calls += 1; throw refusal("legal_acceptance_required", 403); };
    const world = renderWithQueryClient(createElement(Alerts));
    await waitFor(() => assert.equal(world.qc.getQueryCache().getAll()[0].state.status, "error"));
    assert.equal(intervalOf(world.qc), false);
    await new Promise(resolve => setTimeout(resolve, 1300));
    assert.equal(calls, 1, "no retry");
});

test("alerts poll still retries an ordinary 500 and keeps its interval", async () => {
    let calls = 0;
    backendApi.alerts.list = async () => { calls += 1; throw refusal("server_error", 500); };
    const world = renderWithQueryClient(createElement(Alerts));
    await waitFor(() => assert.ok(calls >= 2), { timeout: 4000 });
    await waitFor(() => assert.equal(world.qc.getQueryCache().getAll()[0].state.status, "error"), { timeout: 4000 });
    assert.equal(intervalOf(world.qc), 10_000);
});
