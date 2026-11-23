import React from "react";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { cleanup, render, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace, createHttpClient, resetSessionExpiredLatch, setRequestIdentity } from "@/lib/http-client";
import { SIGNIN_ENDED_FLAG } from "@/lib/signin-ended-flag";
import { AppProviders } from "./app-providers";

const originalGetMe = api.getMe, originalFetch = globalThis.fetch;
afterEach(() => {
    cleanup(); api.getMe = originalGetMe; globalThis.fetch = originalFetch;
    localStorage.clear(); sessionStorage.clear(); clearFreshLoginGrace(); window.history.replaceState({}, "", "/");
});

test("A0: the session-expired handler writes the marker when it redirects,", async () => {
    api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "anonymous", url: "/auth/me", method: "GET" }); };
    globalThis.fetch = (async () => new Response(null, { status: 401 })) as typeof fetch;
    window.history.replaceState({}, "", "/dashboard/campaigns");
    const pushed: string[] = [];
    const router = { bfcacheId: "a0", push: (p: string) => { pushed.push(p); }, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } satisfies NonNullable<React.ContextType<typeof AppRouterContext>>;
    render(<AppRouterContext.Provider value={router}><AppProviders><span /></AppProviders></AppRouterContext.Provider>);
    assert.equal(sessionStorage.getItem(SIGNIN_ENDED_FLAG), null);
    await new Promise(resolve => setTimeout(resolve, 50));
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    resetSessionExpiredLatch();
    const client = createHttpClient({ baseUrl: "http://example.test", getToken: () => "t", setToken: () => {} });
    let outcome: unknown;
    await client.request({ path: "/x", skipAuthRefresh: true }).catch((e: unknown) => { outcome = e; });
    await waitFor(() => assert.equal(pushed.length, 1), { timeout: 1500 }).catch((e) => { throw new Error(`${e.message} outcome=${String((outcome as Error)?.message)}`); });
    assert.ok(sessionStorage.getItem(SIGNIN_ENDED_FLAG));
    assert.match(pushed[0], /^\/auth\/login/);
});
