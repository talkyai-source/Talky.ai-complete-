import { afterEach, mock, test } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AUTH_IDENTITY_EVENT_KEY, AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace, createHttpClient, invalidateRequestIdentity, setTokenProvider } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";
import { useEffectivePermissions } from "@/lib/queries/inbound-queries";

// QA F1-19 (QA-UI-044, QA-UI-067): a full load of /contacts, /recordings, a
// campaign page or /inbound-campaigns/new showed "Please wait while your
// sign-in is checked" or "Permissions could not be verified", and sent no
// request. The page's first reads started after AuthProvider armed the
// identity fence but before /auth/me answered, and the client refused them at
// once. Reads now wait for that check (bounded) and go out as the verified
// account; writes stay refused until then.
const originalGetMe = api.getMe;
const originalFetch = globalThis.fetch;
const accountA = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;
const accountB = { ...accountA, id: "user-b", tenant_id: "tenant-b", email: "b@example.test" };

let queryClient: QueryClient | null = null;
afterEach(() => {
    cleanup();
    queryClient?.clear();
    queryClient = null;
    mock.timers.reset();
    notificationsStore.setIdentity(null);
    api.getMe = originalGetMe;
    globalThis.fetch = originalFetch;
    setTokenProvider(null);
    window.localStorage.clear();
    clearFreshLoginGrace();
});

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (error: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
function recordFetch(body: unknown = { items: ["contact-of-a"] }) {
    const sent: { url: string; headers: Headers }[] = [];
    globalThis.fetch = (async (url, init) => {
        sent.push({ url: String(url), headers: new Headers(init?.headers) });
        return Response.json(body);
    }) as typeof fetch;
    return sent;
}
function unverified(error: unknown) {
    return error instanceof ApiClientError && error.code === "identity_unverified";
}
/** The read's outcome, observed from the start so a later rejection is never unhandled. */
function observe(read: Promise<unknown>) {
    const state: { outcome: unknown } = { outcome: "pending" };
    const settled = read.then(() => { state.outcome = "sent"; }, (error) => { state.outcome = error; });
    return { state, settled };
}
/** AuthProvider is mounted (fence armed) and its /auth/me check is still in flight. */
function mountWhileChecking() {
    const me = deferred<typeof accountA>();
    api.getMe = () => me.promise;
    render(<AuthProvider><span>shell</span></AuthProvider>);
    return me;
}
function settledSoon() {
    return new Promise((resolve) => setTimeout(resolve, 20));
}

for (const kind of ["request", "requestRaw"] as const) {
    test(`a ${kind} read started during the sign-in check waits for it and goes out as the verified account`, async () => {
        const me = mountWhileChecking();
        const sent = recordFetch();
        const client = createHttpClient({ baseUrl: "http://example.test" });
        const read = kind === "request" ? client.request({ path: "/contacts" }) : client.requestRaw({ path: "/contacts" });
        // A write is still refused at once while the check is in flight.
        await assert.rejects(client.request({ path: "/campaigns", method: "POST" }), unverified);
        await settledSoon();
        assert.equal(sent.length, 0, "nothing is sent before /auth/me answers");
        await act(async () => { me.resolve({ ...accountA }); await me.promise; });
        const result = await read;
        const body = result instanceof Response ? await result.json() : result;
        assert.deepEqual(body, { items: ["contact-of-a"] });
        assert.equal(sent.length, 1);
        assert.equal(sent[0].headers.get("X-Talky-Expected-User"), "user-a");
        assert.equal(sent[0].headers.get("X-Talky-Expected-Tenant"), "tenant-a");
    });
}

test("a waiting read fails at once, unsent, when the sign-in check finds no session", async () => {
    const me = mountWhileChecking();
    const sent = recordFetch();
    const read = observe(createHttpClient({ baseUrl: "http://example.test" }).request({ path: "/contacts" }));
    await settledSoon();
    assert.equal(read.state.outcome, "pending", "the read waits for the check instead of failing at once");
    await act(async () => { me.reject(new Error("no session")); await me.promise.catch(() => undefined); });
    await read.settled;
    assert.ok(unverified(read.state.outcome), `outcome: ${String(read.state.outcome)}`);
    assert.equal(sent.length, 0);
});

test("a read waiting through an account change in another tab is never sent", async () => {
    mountWhileChecking();
    const sent = recordFetch();
    const read = observe(createHttpClient({ baseUrl: "http://example.test" }).request({ path: "/contacts" }));
    const next = deferred<typeof accountB>();
    api.getMe = () => next.promise;
    act(() => {
        window.localStorage.setItem(AUTH_IDENTITY_EVENT_KEY, JSON.stringify({ kind: "changed", nonce: "f1-19-switch" }));
        window.dispatchEvent(new window.StorageEvent("storage", {
            key: AUTH_IDENTITY_EVENT_KEY, newValue: window.localStorage.getItem(AUTH_IDENTITY_EVENT_KEY), storageArea: window.localStorage,
        }));
    });
    await read.settled;
    assert.ok(unverified(read.state.outcome), `outcome: ${String(read.state.outcome)}`);
    await act(async () => { next.resolve({ ...accountB }); await next.promise; });
    assert.equal(sent.length, 0);
});

test("a read waits at most fifteen seconds for the sign-in check", async () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    setTokenProvider(() => null);
    invalidateRequestIdentity();
    const sent = recordFetch();
    const read = observe(createHttpClient({ baseUrl: "http://example.test" }).request({ path: "/contacts" }));
    mock.timers.tick(14_999);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(read.state.outcome, "pending");
    mock.timers.tick(1);
    await read.settled;
    assert.ok(unverified(read.state.outcome), `outcome: ${String(read.state.outcome)}`);
    assert.equal(sent.length, 0);
});

test("an aborted waiting read stops waiting and is never sent", async () => {
    setTokenProvider(() => null);
    invalidateRequestIdentity();
    const sent = recordFetch();
    const controller = new AbortController();
    const read = createHttpClient({ baseUrl: "http://example.test" }).request({ path: "/contacts", signal: controller.signal });
    controller.abort();
    await assert.rejects(read, (error: unknown) => error instanceof ApiClientError && error.code === "aborted");
    assert.equal(sent.length, 0);
});

test("the sign-in check itself is never held behind the fence", async () => {
    setTokenProvider(() => null);
    invalidateRequestIdentity();
    const sent = recordFetch({ ok: true });
    await createHttpClient({ baseUrl: "http://example.test" }).request({ path: "/auth/me" });
    assert.equal(sent.length, 1);
});

// The /inbound-campaigns/new and /recordings case: a retry:false permission
// query on a page segment that mounts after the layout's AuthProvider.
function Permissions() {
    const permissions = useEffectivePermissions();
    if (permissions.isError) return <p>Permissions could not be verified</p>;
    if (permissions.isSuccess) return <p>{`permissions: ${permissions.data.permissions.join(",")}`}</p>;
    return <p>checking permissions</p>;
}

test("a page's permission query started during the sign-in check shows the permissions, not an error", async () => {
    const me = deferred<typeof accountA>();
    api.getMe = () => me.promise;
    recordFetch({ permissions: ["recordings:read"] });
    const client = new QueryClient();
    queryClient = client;
    const tree = (page: boolean) => (
        <QueryClientProvider client={client}>
            <AuthProvider>{page ? <Permissions /> : <span>shell</span>}</AuthProvider>
        </QueryClientProvider>
    );
    const view = render(tree(false));
    view.rerender(tree(true));
    await act(async () => { await settledSoon(); });
    assert.equal(view.container.textContent?.includes("Permissions could not be verified"), false);
    await act(async () => { me.resolve({ ...accountA }); await me.promise; });
    await screen.findByText("permissions: recordings:read");
});
