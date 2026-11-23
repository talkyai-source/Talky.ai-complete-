import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { createHttpClient, __resetRefreshStateForTests, __setRefreshTimeoutMsForTests,
    adoptSiblingTabRotation, clearFreshLoginGrace, markFreshLogin, setSessionExpiredHandler,
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

function setup(refresh: () => Response | Promise<Response>) {
    let token: string | null = "old-a";
    const calls = { refresh: 0 };
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    globalThis.fetch = (async (url) => {
        if (new URL(String(url)).pathname === "/auth/refresh") { calls.refresh++; return refresh(); }
        return new Response(null, { status: 401 });
    }) as typeof fetch;
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => token, setToken: v => { token = v; } });
    // The shared in-flight refresh is forgotten one macrotask after it settles: separate page loads are later than that.
    const ask = async () => {
        const outcome = await client.request({ path: "/contacts" }).then(() => null, (e: unknown) => e);
        await new Promise(resolve => setTimeout(resolve, 5));
        return outcome;
    };
    return { calls, ask, setTok: (v: string | null) => { token = v; } };
}

test("B1: after a refused renewal, later 401s do not call /auth/refresh again", async () => {
    const t = setup(() => new Response(null, { status: 401 }));
    await t.ask(); await t.ask(); await t.ask();
    assert.equal(t.calls.refresh, 1);
});

test("B1b: a 403 refusal is remembered too", async () => {
    const t = setup(() => new Response(null, { status: 403 }));
    await t.ask(); await t.ask();
    assert.equal(t.calls.refresh, 1);
});

test("B2: a network failure of /auth/refresh is not a refusal: the next 401 tries again", async () => {
    const t = setup(() => { throw new TypeError("fetch failed"); });
    await t.ask(); await t.ask();
    assert.equal(t.calls.refresh, 2);
});

test("B2b: a 503 from /auth/refresh is not a refusal either", async () => {
    const t = setup(() => new Response(null, { status: 503 }));
    await t.ask(); await t.ask();
    assert.equal(t.calls.refresh, 2);
});

test("B3: a new sign-in clears the memory of a refused renewal", async () => {
    const t = setup(() => new Response(null, { status: 401 }));
    await t.ask(); await t.ask();
    assert.equal(t.calls.refresh, 1);
    markFreshLogin(); clearFreshLoginGrace();
    t.setTok("new-a");
    await t.ask();
    assert.equal(t.calls.refresh, 2);
});

test("B3b: a different identity is not covered by the earlier refusal", async () => {
    const t = setup(() => new Response(null, { status: 401 }));
    await t.ask();
    setRequestIdentity({ userId: "user-b", tenantId: "tenant-b" });
    t.setTok("old-b");
    await t.ask();
    assert.equal(t.calls.refresh, 2);
});

test("B4: adopting a sibling tab's renewed token clears the memory", async () => {
    const t = setup(() => new Response(null, { status: 401 }));
    await t.ask(); await t.ask();
    assert.equal(t.calls.refresh, 1);
    adoptSiblingTabRotation("old-a", "sibling-a");
    t.setTok("sibling-a");
    await t.ask();
    assert.equal(t.calls.refresh, 2);
});
