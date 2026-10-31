import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { ApiClientError, createHttpClient, __resetRefreshStateForTests, __setRefreshTimeoutMsForTests,
    clearFreshLoginGrace, setSessionExpiredHandler, resetSessionExpiredLatch,
    setRequestIdentity, setTokenProvider } from "./http-client";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
    __resetRefreshStateForTests();
    __setRefreshTimeoutMsForTests(null);
    clearFreshLoginGrace();
    setSessionExpiredHandler(null);
    setTokenProvider(null);
    setRequestIdentity(null);
});
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
}
const bearer = (init?: RequestInit) => new Headers(init?.headers).get("authorization");
const fresh = () => Response.json({ access_token: "fresh-a", user_id: "user-a", tenant_id: "tenant-a" });

// talklee-frontend-3: a rotation performed by this client is not an account switch.

test("a request that is still in flight when a sibling request rotates the token keeps its successful response", async () => {
    let token: string | null = "old-a";
    const slow = deferred<Response>();
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url, init) => {
        const path = new URL(String(url)).pathname;
        if (path === "/auth/refresh") return fresh();
        if (path === "/campaigns") return slow.promise;
        return bearer(init) === "Bearer old-a" ? new Response(null, { status: 401 }) : Response.json({ ok: true });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    const create = client.request({ path: "/campaigns", method: "POST", body: { name: "x" } });
    await client.request({ path: "/poll" });
    assert.equal(token, "fresh-a");
    slow.resolve(Response.json({ created: true }));
    assert.deepEqual(await create, { created: true });
});

test("a parallel request whose 401 arrives after the shared refresh finished retries with the rotated token", async () => {
    let token: string | null = "old-a";
    const late = deferred<Response>();
    let refreshes = 0;
    const seen: Array<string | null> = [];
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url, init) => {
        const path = new URL(String(url)).pathname;
        if (path === "/auth/refresh") { refreshes++; return fresh(); }
        seen.push(`${path}:${bearer(init)}`);
        if (bearer(init) === "Bearer fresh-a") return Response.json({ path });
        return path === "/b" ? late.promise : new Response(null, { status: 401 });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    const second = client.request({ path: "/b" });
    await client.request({ path: "/a" });
    late.resolve(new Response(null, { status: 401 }));
    assert.deepEqual(await second, { path: "/b" });
    assert.equal(refreshes, 1);
    assert.ok(seen.includes("/b:Bearer fresh-a"));
});

test("a token swapped to another account is still an identity change, rotation bookkeeping or not", async () => {
    let token: string | null = "old-a";
    const slow = deferred<Response>();
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url) => new URL(String(url)).pathname === "/auth/refresh" ? fresh() : slow.promise) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    const pending = client.request({ path: "/contacts" });
    token = "account-b";
    slow.resolve(Response.json({ contacts: ["a-private"] }));
    await assert.rejects(pending, (error: unknown) => error instanceof ApiClientError && error.code === "identity_changed");
});

// talklee-frontend-5: only a rejected refresh ends the session; a failed or slow one does not.

async function expireWith(refresh: () => Promise<Response> | Response) {
    let token: string | null = "old-a";
    let expired = 0;
    setSessionExpiredHandler(() => { expired++; });
    resetSessionExpiredLatch();
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url) => {
        if (new URL(String(url)).pathname === "/auth/refresh") return refresh();
        return new Response(null, { status: 401 });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    const outcome = await client.request({ path: "/contacts" }).then(() => null, (error: unknown) => error);
    return { outcome, token, expired };
}

for (const status of [500, 502, 503, 504]) {
    test(`a ${status} from /auth/refresh keeps the session and surfaces a retryable error`, async () => {
        const { outcome, token, expired } = await expireWith(() => new Response(null, { status }));
        assert.ok(outcome instanceof ApiClientError);
        assert.equal(outcome.code, "server_error");
        assert.equal(token, "old-a");
        assert.equal(expired, 0);
    });
}

test("a network failure of /auth/refresh keeps the session and surfaces a retryable error", async () => {
    const { outcome, token, expired } = await expireWith(() => { throw new TypeError("fetch failed"); });
    assert.ok(outcome instanceof ApiClientError);
    assert.equal(outcome.code, "network_error");
    assert.equal(token, "old-a");
    assert.equal(expired, 0);
});

for (const status of [401, 403]) {
    test(`a ${status} from /auth/refresh still ends the session`, async () => {
        const { outcome, token, expired } = await expireWith(() => new Response(null, { status }));
        assert.ok(outcome instanceof ApiClientError);
        assert.equal(outcome.status, 401);
        assert.equal(token, null);
        assert.equal(expired, 1);
    });
}

test("a hung /auth/refresh times out, keeps the session, and does not wedge later refreshes", async () => {
    const hang = (init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
    let token: string | null = "old-a";
    let expired = 0;
    let refreshes = 0;
    let hangRefresh = true;
    setSessionExpiredHandler(() => { expired++; });
    resetSessionExpiredLatch();
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    __setRefreshTimeoutMsForTests(30);
    globalThis.fetch = (async (url, init) => {
        if (new URL(String(url)).pathname === "/auth/refresh") { refreshes++; return hangRefresh ? hang(init) : fresh(); }
        return bearer(init) === "Bearer fresh-a" ? Response.json({ ok: true }) : new Response(null, { status: 401 });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    const outcome = await Promise.race([
        client.request({ path: "/contacts" }).then(() => null, (error: unknown) => error),
        new Promise(resolve => setTimeout(() => resolve("still-pending"), 2000)),
    ]);
    assert.ok(outcome instanceof ApiClientError, `expected a settled ApiClientError, got ${String(outcome)}`);
    assert.equal(outcome.code, "network_error");
    assert.equal(token, "old-a");
    assert.equal(expired, 0);
    hangRefresh = false;
    await new Promise(resolve => setTimeout(resolve, 5));
    assert.deepEqual(await client.request({ path: "/contacts" }), { ok: true });
    assert.equal(refreshes, 2);
});

// talklee-frontend-1 (client half): the backend now answers a refresh that presents a
// cookie another tab just rotated with a valid sibling token. The client must take it.

test("a sibling-token refresh response (cookie already rotated by another tab) is adopted and the request retried", async () => {
    let token: string | null = "old-a";
    const seen: Array<string | null> = [];
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url, init) => {
        if (new URL(String(url)).pathname === "/auth/refresh") {
            return Response.json({ access_token: "sibling-a", user_id: "user-a", tenant_id: "tenant-a" });
        }
        seen.push(bearer(init));
        return bearer(init) === "Bearer sibling-a" ? Response.json({ ok: true }) : new Response(null, { status: 401 });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    assert.deepEqual(await client.request({ path: "/contacts" }), { ok: true });
    assert.equal(token, "sibling-a");
    assert.deepEqual(seen, ["Bearer old-a", "Bearer sibling-a"]);
});
