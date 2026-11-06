import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { useLayoutEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { AuthProvider, useAuth } from "./auth-context";
import { authTokenStorageKey } from "./auth-token";
import {
    __resetRefreshStateForTests, clearFreshLoginGrace,
    invalidateRequestIdentity, resetSessionExpiredLatch,
} from "./http-client";
import { resetEmailAuditStoreForTests } from "./email-audit";
import { notificationsStore } from "./notifications";

const account = {
    id: "public-bootstrap-user", tenant_id: "public-bootstrap-tenant",
    email: "public-bootstrap@example.invalid", role: "tenant_admin",
    minutes_remaining: 10, minutes_state: "known",
};
const originalFetch = globalThis.fetch;
const originalFallback = process.env.NEXT_PUBLIC_BEARER_FALLBACK;
let auth: ReturnType<typeof useAuth>;
let requests: { path: string; method: string; credentials: RequestCredentials | undefined; headers: Headers }[];
let respond: (path: string) => Response;

function Probe() {
    const value = useAuth();
    useLayoutEffect(() => { auth = value; });
    return <span data-testid="identity">{value.status}:{value.user?.id ?? "none"}</span>;
}
function tree(pathname: string) {
    return <PathnameContext.Provider value={pathname}><AuthProvider><Probe /></AuthProvider></PathnameContext.Provider>;
}
function unauthorized() {
    return Response.json({ error: { code: "unauthorized", message: "Authentication required" } }, { status: 401 });
}
async function settled() {
    await waitFor(() => assert.equal(auth.loading, false));
}

beforeEach(() => {
    window.localStorage.clear();
    clearFreshLoginGrace();
    __resetRefreshStateForTests();
    resetSessionExpiredLatch();
    invalidateRequestIdentity();
    resetEmailAuditStoreForTests();
    notificationsStore.setIdentity(null);
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    requests = [];
    respond = () => unauthorized();
    // Exercise the real API client and its 401 refresh path. No request leaves
    // this process, including an unexpected application-data request.
    globalThis.fetch = async (input, init) => {
        const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const path = new URL(raw, window.location.origin).pathname;
        requests.push({ path, method: init?.method ?? "GET", credentials: init?.credentials, headers: new Headers(init?.headers) });
        // Record before asserting: the caller may catch a transport error.
        assert.ok(["/auth/me", "/auth/refresh", "/auth/logout"].some(suffix => path.endsWith(suffix)), `Unexpected request: ${path}`);
        return respond(path);
    };
});
afterEach(() => {
    cleanup();
    globalThis.fetch = originalFetch;
    window.localStorage.clear();
    clearFreshLoginGrace();
    __resetRefreshStateForTests();
    resetSessionExpiredLatch();
    invalidateRequestIdentity();
    resetEmailAuditStoreForTests();
    notificationsStore.setIdentity(null);
    if (originalFallback === undefined) delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    else process.env.NEXT_PUBLIC_BEARER_FALLBACK = originalFallback;
});

for (const pathname of ["/", "/pricing/", "/contact/", "/privacy/", "/docs/getting-started/", "/ai-voices/"]) {
    test(`anonymous public page ${pathname} makes no profile or refresh request`, async () => {
        render(tree(pathname));
        await settled();
        assert.equal(screen.getByTestId("identity").textContent, "anonymous:none");
        assert.deepEqual(requests, [], "Public anonymous bootstrap must not cause /auth/me then /auth/refresh");
    });
}

test("public navigation stays quiet and entering a protected page verifies a cookie-only session", async () => {
    const view = render(tree("/pricing/"));
    await settled();
    assert.equal(requests.length, 0);
    view.rerender(tree("/docs/getting-started/"));
    await settled();
    assert.equal(requests.length, 0);
    respond = () => Response.json(account);
    view.rerender(tree("/dashboard/"));
    await screen.findByText(`authenticated:${account.id}`);
    assert.equal(requests.length, 1);
    assert.ok(requests[0].path.endsWith("/auth/me"));
    assert.equal(requests[0].credentials, "include");
    assert.equal(requests[0].headers.get("authorization"), null);
});

test("a protected full-page reload still verifies an existing cookie-only session", async () => {
    respond = () => Response.json(account);
    render(tree("/dashboard/"));
    await screen.findByText(`authenticated:${account.id}`);
    assert.equal(auth.accessToken, null);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].credentials, "include");
});

test("a saved bearer on a public page is verified rather than treated as anonymous", async () => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "true";
    window.localStorage.setItem(authTokenStorageKey(), "synthetic-public-bearer");
    respond = () => Response.json(account);
    render(tree("/pricing/"));
    await screen.findByText(`authenticated:${account.id}`);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].headers.get("authorization"), "Bearer synthetic-public-bearer");
});

test("a verified user can refresh on a public page and rotate an expired access cookie", async () => {
    respond = () => Response.json(account);
    const view = render(tree("/dashboard/"));
    await screen.findByText(`authenticated:${account.id}`);
    view.rerender(tree("/pricing/"));
    await settled();
    assert.equal(auth.user?.id, account.id);
    requests = [];
    let profileReads = 0;
    respond = path => {
        if (path.endsWith("/auth/refresh")) return Response.json({ user_id: account.id, tenant_id: account.tenant_id });
        profileReads += 1;
        return profileReads === 1 ? unauthorized() : Response.json({ ...account, minutes_remaining: 9 });
    };
    await act(async () => { await auth.refreshUser({ silent: true }); });
    assert.deepEqual(requests.map(request => [request.method, request.path.split("/auth/")[1]]), [
        ["GET", "me"], ["POST", "refresh"], ["GET", "me"],
    ]);
    assert.ok(requests.every(request => request.credentials === "include"));
    assert.equal(auth.user?.id, account.id);
    assert.equal(auth.user?.minutes_remaining, 9);
    assert.equal(auth.status, "authenticated");
});

test("a private route sharing a public prefix is still checked", async () => {
    render(tree("/contacts/"));
    await settled();
    assert.deepEqual(requests.map(request => request.path.split("/auth/")[1]), ["me", "refresh"]);
    assert.equal(auth.status, "anonymous");
});

test("public-to-protected navigation cannot reopen profile reads after confirmed logout", async () => {
    respond = path => path.endsWith("/auth/logout") ? Response.json({ message: "Logged out" }) : Response.json(account);
    const view = render(tree("/dashboard/"));
    await screen.findByText(`authenticated:${account.id}`);
    await act(async () => { assert.equal((await auth.logout()).serverConfirmed, true); });
    assert.equal(window.localStorage.getItem("talky.logout.pending"), null, "Exercise the cleared receipt, not just persisted pending logout");
    requests = [];
    // A cookie can still belong to a separate sign-in after local logout.
    // Navigation alone must not regain permission to discover that identity.
    respond = () => Response.json(account);
    view.rerender(tree("/pricing/"));
    await settled();
    view.rerender(tree("/dashboard/"));
    await settled();
    assert.deepEqual(requests, []);
    assert.equal(auth.status, "anonymous");
    assert.equal(auth.user, null);
});
