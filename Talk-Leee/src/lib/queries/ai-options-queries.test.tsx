import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React, { useLayoutEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { aiOptionsApi } from "@/lib/ai-options-api";
import { PrefetchOnAuth } from "@/components/providers/prefetch-on-auth";
import { aiOptionsKeys, useConfigQuery, useProvidersQuery, useVoicesQuery } from "./ai-options-queries";

const accountA = { id: "user-a", tenant_id: "tenant-a", email: "a@example.invalid", role: "tenant_admin", minutes_remaining: 1 };
const accountB = { ...accountA, id: "user-b", tenant_id: "tenant-b", email: "b@example.invalid" };
let auth: ReturnType<typeof useAuth>;
function IdentityOnly() {
    const current = useAuth();
    useLayoutEffect(() => { auth = current; });
    return <span data-testid="identity">{current.user?.id ?? "anonymous"}</span>;
}
function View() {
    const current = useAuth();
    useLayoutEffect(() => { auth = current; });
    const config = useConfigQuery();
    const providers = useProvidersQuery();
    const voices = useVoicesQuery();
    return <div>
        <span data-testid="identity">{current.user?.id ?? "anonymous"}</span>
        <span data-testid="config">{config.data?.llm_model ?? "empty"}</span>
        <span data-testid="catalog">{providers.data?.llm.providers.join(",") ?? "empty"}</span>
        <span data-testid="voices">{voices.data?.voices[0]?.id ?? "empty"}</span>
    </div>;
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
function client() { return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } }); }
function mount(qc: QueryClient, view = true) {
    window.localStorage.setItem("talklee.auth.token", "synthetic-auth-session");
    return render(<AuthProvider><QueryClientProvider client={qc}><PrefetchOnAuth />{view ? <View /> : <IdentityOnly />}</QueryClientProvider></AuthProvider>);
}
afterEach(() => { cleanup(); mock.restoreAll(); window.localStorage.clear(); });

test("AI Options refetches all tenant catalogs on account switch and never reuses A's cached profile", async () => {
    let account = accountA;
    mock.method(api, "getMe", async () => account);
    mock.method(aiOptionsApi, "getConfig", async () => ({ llm_model: account.id }));
    mock.method(aiOptionsApi, "getProviders", async () => ({ llm: { providers: [account.id] } }));
    mock.method(aiOptionsApi, "getVoices", async () => ({ voices: [{ id: account.id }] }));
    const qc = client();
    try {
        mount(qc);
        await waitFor(() => assert.equal(screen.getByTestId("config").textContent, "user-a"));
        account = accountB;
        await act(async () => { await auth.refreshUser(); });
        await waitFor(() => assert.equal(screen.getByTestId("config").textContent, "user-b"));
        assert.equal(screen.getByTestId("catalog").textContent, "user-b");
        assert.equal(screen.getByTestId("voices").textContent, "user-b");
        assert.equal(qc.getQueryData(aiOptionsKeys.config(JSON.stringify(["tenant-a", "user-a"]))), undefined);
    } finally { cleanup(); qc.clear(); }
});

test("late A response cannot become B's config or repopulate the departing cache", async () => {
    let account = accountA;
    const held = deferred<{ llm_model: string }>();
    const requests: string[] = [];
    mock.method(api, "getMe", async () => account);
    mock.method(aiOptionsApi, "getConfig", async () => {
        requests.push(account.id);
        return account.id === "user-a" ? held.promise : { llm_model: account.id };
    });
    mock.method(aiOptionsApi, "getProviders", async () => ({ llm: { providers: [] } }));
    mock.method(aiOptionsApi, "getVoices", async () => ({ voices: [] }));
    const qc = client();
    try {
        mount(qc);
        await waitFor(() => assert.ok(requests.includes("user-a")));
        account = accountB;
        await act(async () => { await auth.refreshUser(); });
        await act(async () => { held.resolve({ llm_model: "user-a" }); await held.promise; });
        await waitFor(() => assert.equal(screen.getByTestId("config").textContent, "user-b"));
        assert.deepEqual(requests, ["user-a", "user-b"]);
        assert.equal(qc.getQueryData(aiOptionsKeys.config(JSON.stringify(["tenant-a", "user-a"]))), undefined);
    } finally { cleanup(); qc.clear(); }
});

test("unverified or tenantless identities cannot start AI Options queries or prefetch", async () => {
    const me = deferred<typeof accountA>();
    mock.method(api, "getMe", () => me.promise);
    const config = mock.method(aiOptionsApi, "getConfig", async () => ({ llm_model: "unexpected" }));
    const providers = mock.method(aiOptionsApi, "getProviders", async () => ({ llm: { providers: [] } }));
    const voices = mock.method(aiOptionsApi, "getVoices", async () => ({ voices: [] }));
    const qc = client();
    try {
        mount(qc);
        await act(async () => { await Promise.resolve(); });
        assert.equal(config.mock.callCount(), 0);
        await act(async () => { me.resolve({ ...accountA, tenant_id: "" }); await me.promise; });
        await waitFor(() => assert.equal(screen.getByTestId("identity").textContent, "user-a"));
        assert.equal(config.mock.callCount(), 0);
        assert.equal(providers.mock.callCount(), 0);
        assert.equal(voices.mock.callCount(), 0);
    } finally { cleanup(); qc.clear(); }
});

test("post-login prefetch reruns for a different tenant even when the user ID is unchanged and no editor is mounted", async () => {
    let account = accountA;
    mock.method(api, "getMe", async () => account);
    const requests: string[] = [];
    mock.method(aiOptionsApi, "getConfig", async () => { requests.push(account.tenant_id); return { llm_model: account.tenant_id }; });
    mock.method(aiOptionsApi, "getProviders", async () => ({ llm: { providers: [] } }));
    mock.method(aiOptionsApi, "getVoices", async () => ({ voices: [] }));
    // Prefetch has no observer: retain its cache until this test clears it.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    try {
        mount(qc, false);
        await waitFor(() => assert.deepEqual(requests, ["tenant-a"]));
        account = { ...accountA, tenant_id: "tenant-b" };
        await act(async () => { await auth.refreshUser(); });
        await waitFor(() => assert.deepEqual(requests, ["tenant-a", "tenant-b"]));
        assert.deepEqual(qc.getQueryData(aiOptionsKeys.config(JSON.stringify(["tenant-b", "user-a"]))), { llm_model: "tenant-b" });
        assert.equal(qc.getQueryData(aiOptionsKeys.config(JSON.stringify(["tenant-a", "user-a"]))), undefined);
    } finally { cleanup(); qc.clear(); }
});

test("Strict Mode effect replay with an already verified account leaves active query observers usable", async () => {
    mock.method(api, "getMe", async () => accountA);
    const first = deferred<{ llm_model: string }>();
    let requests = 0;
    mock.method(aiOptionsApi, "getConfig", async () => ++requests === 1 ? first.promise : { llm_model: "verified-a" });
    mock.method(aiOptionsApi, "getProviders", async () => ({ llm: { providers: ["verified-a"] } }));
    mock.method(aiOptionsApi, "getVoices", async () => ({ voices: [{ id: "verified-a" }] }));
    const qc = client();
    function VerifiedChildren() {
        const current = useAuth();
        return current.user && !current.loading
            ? <React.StrictMode><PrefetchOnAuth /><View /></React.StrictMode> : null;
    }
    try {
        window.localStorage.setItem("talklee.auth.token", "synthetic-auth-session");
        render(<AuthProvider><QueryClientProvider client={qc}><VerifiedChildren /></QueryClientProvider></AuthProvider>);
        await waitFor(() => assert.equal(screen.getByTestId("config").textContent, "verified-a"));
        await act(async () => { first.resolve({ llm_model: "discarded-first-attempt" }); await first.promise; });
        assert.equal(screen.getByTestId("config").textContent, "verified-a");
        assert.equal(screen.getByTestId("catalog").textContent, "verified-a");
        assert.equal(screen.getByTestId("voices").textContent, "verified-a");
        assert.ok(requests >= 2);
        assert.deepEqual(qc.getQueryData(aiOptionsKeys.config(JSON.stringify(["tenant-a", "user-a"]))), { llm_model: "verified-a" });
    } finally { cleanup(); qc.clear(); }
});
