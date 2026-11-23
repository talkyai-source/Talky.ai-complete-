import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { createAppQueryClient } from "@/lib/app-query-client";
import { ApiClientError } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { rememberSignedIn } from "@/lib/sign-in-ended";

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
    try {
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
            try {
                await assert.rejects(() => mutation.execute(undefined));
                assert.equal(calls, 1, `mutationFn must run exactly once for ${error.message}`);
            } finally {
                // Clearing cache membership alone does not cancel this timer.
                mutation.destroy();
            }
        }
    } finally {
        qc.clear();
    }
});

test("a mutation whose meta.inlineError owns the failure raises no toast; other failures still do", async () => {
    const qc = createAppQueryClient(() => {});
    const toasts = () => notificationsStore.getSnapshot().toasts.map((t) => `${t.title}: ${t.message}`);
    const refusal = new ApiClientError({ code: "test_billing_team_only", message: "Payments are not open yet.", status: 403, url: "/billing/create-checkout-session", method: "POST" });
    const other = new ApiClientError({ code: "http_error", message: "Package not found", status: 400, url: "/billing/topups/checkout", method: "POST" });
    try {
        for (const [error, expected] of [[refusal, []], [other, ["Request failed: Package not found"]]] as const) {
            const mutation = qc.getMutationCache().build(qc, {
                mutationFn: async () => { throw error; },
                meta: { inlineError: (failure: unknown) => failure === refusal },
            });
            try {
                await assert.rejects(() => mutation.execute(undefined));
                assert.deepEqual(toasts(), expected);
            } finally { mutation.destroy(); notificationsStore.clearAll(); }
        }
    } finally {
        qc.clear();
    }
});

test("queries keep their bounded retry", () => {
    const qc = createAppQueryClient(() => {});
    try {
        const retry = qc.getDefaultOptions().queries?.retry;
        assert.equal(typeof retry, "function");
        const fn = retry as (count: number, err: Error) => boolean;
        assert.equal(fn(0, serverError()), true);
        assert.equal(fn(2, serverError()), false);
    } finally {
        qc.clear();
    }
});

test("an unauthorized query or mutation error writes no sign-in-ended marker", async () => {
    sessionStorage.clear();
    rememberSignedIn();
    const qc = createAppQueryClient(() => {});
    const unauthorized = new ApiClientError({ code: "unauthorized", message: "gone", status: 401, url: "/x", method: "GET" });
    try {
        const mutation = qc.getMutationCache().build(qc, { mutationFn: async () => { throw unauthorized; } });
        try {
            await assert.rejects(() => mutation.execute(undefined));
        } finally { mutation.destroy(); notificationsStore.clearAll(); }
        assert.equal(sessionStorage.getItem("talky.signin.tab"), "signed-in");
    } finally {
        qc.clear();
        sessionStorage.clear();
    }
});

test("legal_acceptance_required: no retry and no toast; forbidden and a 500 behave as before", async () => {
    const qc = createAppQueryClient(() => {});
    const toasts = () => notificationsStore.getSnapshot().toasts.map((t) => t.title);
    const legal = new ApiClientError({ code: "legal_acceptance_required", message: "Accept the terms", status: 403, url: "/campaigns", method: "GET" });
    const forbidden = new ApiClientError({ code: "forbidden", message: "no", status: 403, url: "/campaigns", method: "GET" });
    try {
        const retry = qc.getDefaultOptions().queries?.retry as (count: number, err: Error) => boolean;
        assert.equal(retry(0, legal), false);
        assert.equal(retry(0, forbidden), false);
        assert.equal(retry(0, serverError()), true);
        for (const [error, expected] of [[legal, []], [forbidden, ["Permission denied"]], [serverError(), ["Server error"]]] as const) {
            const mutation = qc.getMutationCache().build(qc, { mutationFn: async () => { throw error; } });
            try {
                await assert.rejects(() => mutation.execute(undefined));
                assert.deepEqual(toasts(), expected);
            } finally { mutation.destroy(); notificationsStore.clearAll(); }
        }
    } finally {
        qc.clear();
    }
});
