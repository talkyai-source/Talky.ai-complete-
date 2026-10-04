import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { MutationObserver, QueryClientProvider } from "@tanstack/react-query";
import { createAppQueryClient } from "@/lib/app-query-client";
import { ApiClientError } from "@/lib/http-client";
import { notificationMutationOptions } from "@/lib/notification-mutations";
import { notificationsStore } from "@/lib/notifications";
import { backendApi } from "@/lib/backend-api";
import { useTestSalesforceConnection } from "@/lib/api-hooks";
import { inboundApi } from "@/lib/inbound-api";
import { inboundQueryKeys, useSetTenantInboundControls } from "@/lib/queries/inbound-queries";

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

const identityA = { tenantId: "tenant-a", userId: "user-a" };
const identityB = { tenantId: "tenant-b", userId: "user-b" };

beforeEach(() => {
    window.localStorage.clear();
    notificationsStore.setIdentity(null);
    notificationsStore.setIdentity(identityA);
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    notificationsStore.setIdentity(null);
    window.localStorage.clear();
});

function client() {
    const qc = createAppQueryClient(() => {});
    qc.setDefaultOptions({ queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 } });
    return qc;
}

for (const outcome of ["success", "failure"] as const) {
    test(`a delayed mutation ${outcome} cannot notify B after observer callbacks are replaced`, async () => {
        const qc = client();
        const result = deferred<string>();
        const started = deferred<void>();
        const callbacks: string[] = [];
        const options = (render: string) => notificationMutationOptions({
            mutationFn: async (_input: string) => { started.resolve(); return result.promise; },
            onMutate: () => ({ previous: "rollback-a" }),
            onSuccess: (data: string) => { callbacks.push(render); notificationsStore.create({ type: "success", title: data }); },
            onError: (error: Error) => { callbacks.push(render); notificationsStore.create({ type: "error", title: error.message }); },
            onSettled: () => { callbacks.push(`${render}-settled`); },
        });
        const observer = new MutationObserver(qc, options("A"));
        try {
            const pending = observer.mutate("a-record");
            const checked = outcome === "failure" ? assert.rejects(pending, /private A failure/) : pending;
            await started.promise;
            notificationsStore.setIdentity(identityB);
            // This updates the in-flight mutation's options in actual TanStack.
            observer.setOptions(options("B"));
            if (outcome === "failure") result.reject(new Error("private A failure"));
            else result.resolve("private A result");
            await checked;
            assert.deepEqual(callbacks, []);
            assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
            assert.deepEqual(notificationsStore.getSnapshot().toasts, []);
        } finally { qc.clear(); }
    });
}

test("the cache captures mutation origin before its first await, including immediate switches", async () => {
    const qc = client();
    const observer = new MutationObserver(qc, notificationMutationOptions({
        mutationFn: async () => "A result",
        onSuccess: (value) => { notificationsStore.create({ type: "success", title: value }); },
    }));
    try {
        const pending = observer.mutate();
        notificationsStore.setIdentity(identityB);
        await pending;
        assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
    } finally { qc.clear(); }
});

test("same-owner completion preserves onMutate context, rollback, and notification", async () => {
    const qc = client();
    const previous = { saved: "original" };
    qc.setQueryData(["test-record"], previous);
    const observer = new MutationObserver(qc, notificationMutationOptions({
        mutationFn: async () => { throw new Error("failed"); },
        onMutate: async () => {
            await Promise.resolve();
            qc.setQueryData(["test-record"], { saved: "optimistic" });
            return { previous };
        },
        onError: (_error, _input, context) => {
            assert.equal(context?.previous, previous);
            qc.setQueryData(["test-record"], context?.previous);
            notificationsStore.create({ type: "error", title: "Could not save" });
        },
    }));
    try {
        await assert.rejects(observer.mutate(), /failed/);
        assert.deepEqual(qc.getQueryData(["test-record"]), previous);
        assert.ok(notificationsStore.getSnapshot().notifications.some((entry) => entry.title === "Could not save"));
    } finally { qc.clear(); }
});

test("verified same-identity refresh preserves the pending operation origin", async () => {
    const qc = client();
    const result = deferred<string>();
    const observer = new MutationObserver(qc, notificationMutationOptions({
        mutationFn: () => result.promise,
        onSuccess: (value) => { notificationsStore.create({ type: "success", title: value }); },
    }));
    try {
        const pending = observer.mutate();
        notificationsStore.setIdentity({ ...identityA });
        result.resolve("Saved for A");
        await pending;
        assert.equal(notificationsStore.getSnapshot().notifications[0]?.title, "Saved for A");
    } finally { qc.clear(); }
});

test("logout followed by the same user logging in does not revive old operation callbacks", async () => {
    const qc = client();
    const result = deferred<string>();
    const observer = new MutationObserver(qc, notificationMutationOptions({
        mutationFn: () => result.promise,
        onSuccess: (value) => { notificationsStore.create({ type: "success", title: value }); },
    }));
    try {
        const pending = observer.mutate();
        notificationsStore.setIdentity(null);
        notificationsStore.setIdentity(identityA);
        result.resolve("Old session result");
        await pending;
        assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
    } finally { qc.clear(); }
});

function requestError(message: string, code = "invalid_request") {
    return new ApiClientError({ code, message, status: 400, url: "http://localhost/test", method: "GET" });
}

test("a delayed global query error is silent after a tenant switch, then a fresh B fetch notifies", async () => {
    const qc = client();
    const result = deferred<string>();
    try {
        const pending = qc.fetchQuery({ queryKey: ["same-shared-key"], queryFn: () => result.promise });
        const checked = assert.rejects(pending, /private A error/);
        notificationsStore.setIdentity({ userId: identityA.userId, tenantId: identityB.tenantId });
        result.reject(requestError("private A error"));
        await checked;
        assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
        await assert.rejects(qc.fetchQuery({ queryKey: ["same-shared-key"], queryFn: async () => { throw requestError("B error"); } }), /B error/);
        assert.equal(notificationsStore.getSnapshot().notifications[0]?.message, "B error");
    } finally { qc.clear(); }
});

test("global mutation errors without local notification hooks remain bound to their start", async () => {
    const qc = client();
    const result = deferred<string>();
    const observer = new MutationObserver(qc, { mutationFn: () => result.promise });
    try {
        const pending = observer.mutate();
        const checked = assert.rejects(pending, /private A error/);
        notificationsStore.setIdentity(identityB);
        result.reject(requestError("private A error"));
        await checked;
        assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
    } finally { qc.clear(); }
});

test("health failures and unverified identities cannot publish global errors", async () => {
    const qc = client();
    try {
        await assert.rejects(qc.fetchQuery({ queryKey: ["health"], queryFn: async () => { throw requestError("health failure"); } }));
        notificationsStore.setIdentity(null);
        await assert.rejects(qc.fetchQuery({ queryKey: ["anonymous"], queryFn: async () => { throw requestError("anonymous failure"); } }));
        notificationsStore.setIdentity(identityB);
        assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
    } finally { qc.clear(); }
});

test("the actual Salesforce hook drops a delayed A error after rerender in B", async () => {
    const qc = client();
    const response = deferred<Awaited<ReturnType<typeof backendApi.salesforce.test>>>();
    const started = deferred<void>();
    mock.method(backendApi.salesforce, "test", () => { started.resolve(); return response.promise; });
    const hook = renderHook(() => useTestSalesforceConnection(), {
        wrapper: ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: qc }, children),
    });
    try {
        let pending!: ReturnType<typeof hook.result.current.mutateAsync>;
        act(() => { pending = hook.result.current.mutateAsync(); });
        const checked = assert.rejects(pending, /A private connection error/);
        await started.promise;
        act(() => { notificationsStore.setIdentity(identityB); });
        hook.rerender();
        await act(async () => { response.reject(new Error("A private connection error")); await checked; });
        assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
    } finally { hook.unmount(); qc.clear(); }
});

test("the actual inbound mutation drops a stale success and preserves the new tenant cache", async () => {
    const qc = client();
    qc.setQueryDefaults(inboundQueryKeys.controls, { gcTime: Infinity });
    const response = deferred<Awaited<ReturnType<typeof inboundApi.setControls>>>();
    const started = deferred<void>();
    mock.method(inboundApi, "setControls", () => { started.resolve(); return response.promise; });
    const hook = renderHook(() => useSetTenantInboundControls(), {
        wrapper: ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client: qc }, children),
    });
    try {
        let pending!: ReturnType<typeof hook.result.current.mutateAsync>;
        act(() => { pending = hook.result.current.mutateAsync({ inbound_enabled: true, expected_version: 1, reason: "A request" }); });
        await started.promise;
        act(() => { notificationsStore.setIdentity(identityB); });
        const tenantB = { inbound_enabled: false, version: 7, reason: "B current state", updated_at: null };
        qc.setQueryData(inboundQueryKeys.controls, tenantB);
        hook.rerender();
        await act(async () => {
            response.resolve({ inbound_enabled: true, version: 2, reason: "A request", updated_at: null });
            await pending;
        });
        assert.deepEqual(qc.getQueryData(inboundQueryKeys.controls), tenantB);
        assert.deepEqual(notificationsStore.getSnapshot().notifications, []);
    } finally { hook.unmount(); qc.clear(); }
});
