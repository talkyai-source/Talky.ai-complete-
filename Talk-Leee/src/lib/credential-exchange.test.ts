import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { api } from "@/lib/api";
import {
    ApiClientError, createHttpClient, __resetRefreshStateForTests,
    clearFreshLoginGrace, resetSessionExpiredLatch, setSessionExpiredHandler,
} from "@/lib/http-client";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
    setSessionExpiredHandler(null);
    resetSessionExpiredLatch();
    __resetRefreshStateForTests();
});

test("rejected login, signup, reset and second-factor credentials never refresh or clear another session", async () => {
    for (const path of ["/auth/login", "/auth/signup/start", "/auth/signup/verify-code",
        "/auth/signup/complete", "/auth/verify-otp", "/auth/forgot-password", "/auth/reset-password",
        "/auth/mfa/verify", "/auth/passkeys/login/begin", "/auth/passkeys/login/complete"]) {
        __resetRefreshStateForTests();
        resetSessionExpiredLatch();
        clearFreshLoginGrace();
        const calls: string[] = [];
        let clears = 0;
        let redirects = 0;
        setSessionExpiredHandler(() => redirects++);
        globalThis.fetch = async (input) => {
            calls.push(String(input));
            return new Response(JSON.stringify({ detail: "Invalid credentials" }), {
                status: 401, headers: { "content-type": "application/json" },
            });
        };
        const client = createHttpClient({ baseUrl: "https://example.test/api/v1",
            getToken: () => "existing-session", setToken: () => clears++ });
        await assert.rejects(client.request({ path, method: "POST", body: { code: "000000" } }),
            (error: unknown) => error instanceof ApiClientError && error.status === 401);
        assert.deepEqual(calls, [`https://example.test/api/v1${path}`], path);
        assert.equal(clears, 0, path);
        assert.equal(redirects, 0, path);
    }
});

test("an explicit callback token is checked once without refreshing the previous account", async () => {
    const calls: { url: string; authorization: string | null }[] = [];
    globalThis.fetch = async (input, init) => {
        calls.push({ url: String(input), authorization: new Headers(init?.headers).get("authorization") });
        return new Response(JSON.stringify({ detail: "Invalid token" }), {
            status: 401, headers: { "content-type": "application/json" },
        });
    };
    const client = createHttpClient({ baseUrl: "https://example.test/api/v1", getToken: () => "previous-account" });
    await assert.rejects(client.request({ path: "/auth/me", headers: { Authorization: "Bearer callback-token" },
        skipAuthRefresh: true, suppressAuthRedirect: true }));
    assert.deepEqual(calls, [{ url: "https://example.test/api/v1/auth/me", authorization: "Bearer callback-token" }]);
});

test("protected account requests retain refresh and session-expiry handling", async () => {
    __resetRefreshStateForTests();
    resetSessionExpiredLatch();
    clearFreshLoginGrace();
    const calls: string[] = [];
    let cleared = 0;
    let expired = 0;
    setSessionExpiredHandler(() => expired++);
    globalThis.fetch = async (input) => {
        calls.push(String(input));
        return new Response(null, { status: 401 });
    };
    const client = createHttpClient({ baseUrl: "https://example.test/api/v1", getToken: () => "expired",
        setToken: () => cleared++ });
    await assert.rejects(client.request({ path: "/auth/me" }));
    assert.deepEqual(calls, ["https://example.test/api/v1/auth/me", "https://example.test/api/v1/auth/refresh"]);
    assert.equal(cleared, 1);
    assert.equal(expired, 1);
});

test("callback account verification never falls back to a legacy identity endpoint", async () => {
    for (const status of [401, 404]) {
        const calls: { path: string; authorization: string | null }[] = [];
        globalThis.fetch = async (input, init) => {
            calls.push({ path: new URL(String(input), "http://localhost").pathname,
                authorization: new Headers(init?.headers).get("authorization") });
            return new Response(JSON.stringify({ detail: "Account not available" }), {
                status, headers: { "content-type": "application/json" },
            });
        };
        await assert.rejects(api.getMe("link-token"), (error: unknown) => error instanceof ApiClientError && error.status === status);
        assert.equal(calls.length, 1);
        assert.ok(calls[0]?.path.endsWith("/auth/me"));
        assert.equal(calls[0]?.authorization, "Bearer link-token");
    }
});
