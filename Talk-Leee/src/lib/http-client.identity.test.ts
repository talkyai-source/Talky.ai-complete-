import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { api } from "./api";
import { getBrowserAuthToken } from "./auth-token";
import { ApiClientError, createHttpClient, __resetRefreshStateForTests,
    clearFreshLoginGrace, setSessionExpiredHandler, resetSessionExpiredLatch,
    setRequestIdentity, setTokenProvider, invalidateRequestIdentity } from "./http-client";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
    __resetRefreshStateForTests();
    clearFreshLoginGrace();
    setSessionExpiredHandler(null);
    setTokenProvider(null);
    localStorage.clear();
});
function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
}
function identityChanged(error: unknown) {
    assert.ok(error instanceof ApiClientError);
    assert.equal(error.code, "identity_changed");
    return true;
}

for (const raw of [false, true]) {
    test(`${raw ? "binary" : "JSON"} request cannot retry as the account that logged in while it was pending`, async () => {
        __resetRefreshStateForTests();
        const response = deferred<Response>();
        let token: string | null = "account-a";
        const effects: string[] = [];
        const requests: string[] = [];
        globalThis.fetch = (async (url, init) => {
            requests.push(String(url));
            if (requests.length === 1) return response.promise;
            if (String(url).endsWith("/auth/refresh")) return new Response(null, { status: 204 });
            effects.push(new Headers(init?.headers).get("authorization") ?? "cookie");
            return Response.json({ saved: true });
        }) as typeof fetch;
        const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: (value) => { token = value; } });
        const pending = raw ? client.requestRaw({ path: "/settings", method: "PUT", body: { voice: "reviewed-by-a" } })
            : client.request({ path: "/settings", method: "PUT", body: { voice: "reviewed-by-a" } });
        token = "account-b";
        response.resolve(Response.json({ detail: "expired" }, { status: 401 }));
        await assert.rejects(pending, identityChanged);
        assert.deepEqual(effects, []);
        assert.equal(requests.length, 1);
        assert.equal(token, "account-b");
    });
}

test("an old failed response cannot clear the newer token or fire its session-expired handler", async () => {
    const response = deferred<Response>();
    let token: string | null = "account-a";
    let expired = 0;
    setSessionExpiredHandler(() => { expired += 1; });
    resetSessionExpiredLatch();
    globalThis.fetch = (async (url) => String(url).endsWith("/auth/refresh")
        ? Response.json({ detail: "expired" }, { status: 401 }) : response.promise) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: (value) => { token = value; } });
    const pending = client.request({ path: "/contacts" });
    token = "account-b";
    response.resolve(Response.json({ detail: "expired" }, { status: 401 }));
    await assert.rejects(pending, identityChanged);
    assert.equal(token, "account-b");
    assert.equal(expired, 0);
});

test("an old successful response is discarded after the local identity changes", async () => {
    const response = deferred<Response>();
    let token = "account-a";
    globalThis.fetch = (() => response.promise) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token });
    const pending = client.request({ path: "/contacts" });
    token = "account-b";
    response.resolve(Response.json({ contacts: ["account-a-private-contact"] }));
    await assert.rejects(pending, identityChanged);
});

test("late API logout completion cannot erase a later sign-in", async () => {
    const response = deferred<Response>();
    globalThis.fetch = (() => response.promise) as typeof fetch;
    api.setToken("account-a");
    const pending = api.logout();
    api.setToken("account-b");
    response.resolve(new Response(null, { status: 204 }));
    await pending.catch(() => undefined);
    assert.equal(getBrowserAuthToken(), "account-b");
});

test("unverified account transition stops protected writes until the new identity is checked", async () => {
    let calls = 0;
    setTokenProvider(() => null);
    invalidateRequestIdentity();
    globalThis.fetch = (async () => { calls++; return Response.json({ saved: true }); }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test" });
    await assert.rejects(client.request({ path: "/campaigns", method: "POST" }),
        (error: unknown) => error instanceof ApiClientError && error.code === "identity_unverified");
    assert.equal(calls, 0);
    setRequestIdentity({ userId: "user-b", tenantId: "tenant-b" });
    await client.request({ path: "/campaigns", method: "POST" });
    assert.equal(calls, 1);
});

test("wrong login credentials do not refresh or sign out an existing session", async () => {
    let token: string | null = "account-a";
    let expired = 0;
    const paths: string[] = [];
    setSessionExpiredHandler(() => { expired++; });
    globalThis.fetch = (async (url) => { paths.push(String(url)); return Response.json({ detail: "Wrong password" }, { status: 401 }); }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    await assert.rejects(client.request({ path: "/auth/login", method: "POST" }));
    assert.deepEqual(paths, ["http://example.test/auth/login"]);
    assert.equal(token, "account-a");
    assert.equal(expired, 0);
});

test("same-identity parallel requests share rotation and retry with the new bearer and expected tenant", async () => {
    let token: string | null = "old-a";
    const renewal = deferred<Response>();
    let refreshes = 0;
    let effects = 0;
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url, init) => {
        const headers = new Headers(init?.headers);
        assert.equal(headers.get("X-Talky-Expected-User"), "user-a");
        assert.equal(headers.get("X-Talky-Expected-Tenant"), "tenant-a");
        if (String(url).endsWith("/auth/refresh")) { refreshes++; return renewal.promise; }
        if (headers.get("authorization") === "Bearer old-a") return new Response(null, { status: 401 });
        assert.equal(headers.get("authorization"), "Bearer fresh-a"); effects++;
        return Response.json({ saved: true });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: value => { token = value; } });
    const first = client.request({ path: "/first" }); const second = client.request({ path: "/second" });
    await new Promise(resolve => setTimeout(resolve, 0));
    renewal.resolve(Response.json({ access_token: "fresh-a", user_id: "user-a", tenant_id: "tenant-a" }));
    await Promise.all([first, second]);
    assert.equal(refreshes, 1); assert.equal(effects, 2);
});

test("logout endpoint absence remains unconfirmed instead of reporting success", async () => {
    globalThis.fetch = (async () => new Response(null, { status: 404 })) as typeof fetch;
    await assert.rejects(api.logout(), (error: unknown) => error instanceof ApiClientError && error.status === 404);
});

for (const method of ["blob", "json"] as const) test(`a delayed ${method} body cannot expose the previous account after response headers arrive`, async () => {
    let token = "account-a";
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    globalThis.fetch = (async () => new Response(new ReadableStream<Uint8Array>({ start(controller) { stream = controller; } }),
        { headers: { "content-type": "application/json" } })) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token });
    const response = await client.requestRaw({ path: "/recordings/private" });
    const body = response[method]();
    token = "account-b";
    stream.enqueue(new TextEncoder().encode('{"private":"account-a"}')); stream.close();
    await assert.rejects(body, identityChanged);
});
