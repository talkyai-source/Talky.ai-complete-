/**
 * FX1a Task 1 — a request made before the first sign-in check finishes waits
 * for it instead of failing with identity_unverified.
 */
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { ApiClientError, beginRequestIdentityCheck, createHttpClient, __resetRefreshStateForTests,
    setRequestIdentity, setTokenProvider } from "./http-client";

const originalFetch = globalThis.fetch;
let settle: (() => void) | null = null;
afterEach(() => {
    settle?.();
    settle = null;
    globalThis.fetch = originalFetch;
    __resetRefreshStateForTests();
    setTokenProvider(null);
    localStorage.clear();
});
const tick = () => new Promise((resolve) => setTimeout(resolve, 20));

for (const raw of [false, true]) {
    test(`${raw ? "binary" : "JSON"} request made during the sign-in check is sent once the check verifies the user`, async () => {
        const seen: Headers[] = [];
        globalThis.fetch = (async (_url, init) => { seen.push(new Headers(init?.headers)); return Response.json({ campaigns: [] }); }) as typeof fetch;
        setTokenProvider(() => null);
        settle = beginRequestIdentityCheck();
        const client = createHttpClient({ baseUrl: "http://example.test" });
        let failed: unknown = null;
        const pending = (raw ? client.requestRaw({ path: "/campaigns" }) : client.request({ path: "/campaigns" }))
            .catch((error: unknown) => { failed = error; });
        await tick();
        assert.equal(failed, null, "the request must not fail while the check is pending");
        assert.equal(seen.length, 0, "nothing is sent before the identity is verified");
        setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
        settle();
        await pending;
        assert.equal(failed, null);
        assert.equal(seen.length, 1);
        assert.equal(seen[0].get("X-Talky-Expected-User"), "user-a");
        assert.equal(seen[0].get("X-Talky-Expected-Tenant"), "tenant-a");
    });
}

test("a request made during the sign-in check still fails as before when the check finds no user", async () => {
    let calls = 0;
    globalThis.fetch = (async () => { calls++; return Response.json({}); }) as typeof fetch;
    setTokenProvider(() => null);
    settle = beginRequestIdentityCheck();
    const client = createHttpClient({ baseUrl: "http://example.test" });
    const pending = client.request({ path: "/campaigns" });
    settle();
    await assert.rejects(pending, (error: unknown) => error instanceof ApiClientError && error.code === "identity_unverified");
    assert.equal(calls, 0);
});

test("the sign-in check itself is never held back", async () => {
    const paths: string[] = [];
    globalThis.fetch = (async (url) => { paths.push(String(url)); return Response.json({ id: "user-a" }); }) as typeof fetch;
    setTokenProvider(() => null);
    settle = beginRequestIdentityCheck();
    const client = createHttpClient({ baseUrl: "http://example.test" });
    await client.request({ path: "/auth/me" });
    assert.deepEqual(paths, ["http://example.test/auth/me"]);
});
