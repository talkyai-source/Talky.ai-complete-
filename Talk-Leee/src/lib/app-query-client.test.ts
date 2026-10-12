import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { createAppQueryClient } from "@/lib/app-query-client";
import { ApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

beforeEach(() => {
    window.localStorage.clear();
    notificationsStore.setIdentity(null);
    notificationsStore.setIdentity({ tenantId: "tenant-a", userId: "user-a" });
});

afterEach(() => {
    mock.restoreAll();
    notificationsStore.setIdentity(null);
    window.localStorage.clear();
});

function serverError() {
    return new ApiClientError({
        code: "server_error",
        message: "Check your purchase history before trying again",
        status: 502,
        url: "/billing/topups",
        method: "POST",
    });
}

test("a failed mutation is never replayed (non-idempotent POSTs)", async () => {
    const qc = createAppQueryClient(() => {});
    // Only the retry *delay* is shortened so a replay would be observable fast.
    qc.setDefaultOptions({
        ...qc.getDefaultOptions(),
        mutations: { ...qc.getDefaultOptions().mutations, retryDelay: 1 },
    });
    for (const error of [serverError(), new Error("network down"), new ApiClientError({
        code: "network_error", message: "offline", url: "/x", method: "POST",
    })]) {
        let calls = 0;
        const mutation = qc.getMutationCache().build(qc, {
            mutationFn: async () => {
                calls += 1;
                throw error;
            },
        });
        await assert.rejects(() => mutation.execute(undefined));
        assert.equal(calls, 1, `mutationFn must run exactly once for ${error.message}`);
    }
    qc.clear();
});

test("queries keep their bounded retry", () => {
    const qc = createAppQueryClient(() => {});
    const retry = qc.getDefaultOptions().queries?.retry;
    assert.equal(typeof retry, "function");
    const fn = retry as (count: number, err: Error) => boolean;
    assert.equal(fn(0, serverError()), true);
    assert.equal(fn(2, serverError()), false);
    qc.clear();
});
