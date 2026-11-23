import React, { useEffect } from "react";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace } from "@/lib/http-client";
import { SIGNIN_ENDED_FLAG } from "@/lib/signin-ended-flag";
import LoginClientPage from "./login-client";

const ENDED = "Your sign-in ended, so you need to sign in again. This happens after 30 minutes without activity, or when a sign-in reaches its time limit.";
const UNCONFIRMED = /You signed out on this device, but we could not confirm/;
const originalGetMe = api.getMe;
let client: QueryClient | undefined;
const probe = { loading: true };
function Probe() { const value = useAuth(); useEffect(() => { probe.loading = value.loading; }); return null; }
afterEach(() => {
    cleanup(); client?.clear(); client = undefined;
    api.getMe = originalGetMe; localStorage.clear(); sessionStorage.clear(); clearFreshLoginGrace();
    window.history.replaceState({}, "", "/");
});

async function visit(search = "") {
    api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "anonymous", url: "/auth/me", method: "GET" }); };
    window.history.replaceState({}, "", `/auth/login${search}`);
    const router = { bfcacheId: "ended", push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } satisfies NonNullable<React.ContextType<typeof AppRouterContext>>;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    render(<AppRouterContext.Provider value={router}><PathnameContext.Provider value="/auth/login">
        <SearchParamsContext.Provider value={new URLSearchParams(search)}><QueryClientProvider client={client}>
            <AuthProvider><Probe /><LoginClientPage /></AuthProvider>
        </QueryClientProvider></SearchParamsContext.Provider>
    </PathnameContext.Provider></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(probe.loading, false));
    await screen.findByText("Welcome back");
}

test("A1: after the session-expired redirect the page shows the sign-in-ended line", async () => {
    sessionStorage.setItem(SIGNIN_ENDED_FLAG, String(Date.now()));
    await visit();
    assert.ok(await screen.findByText(ENDED));
});

test("A2: an ordinary visit shows no sign-in-ended line", async () => {
    await visit();
    assert.equal(screen.queryByText(ENDED), null);
});

test("A2b: a query parameter from another site cannot switch the line on", async () => {
    await visit("?ended=1&signin=ended&reason=expired");
    assert.equal(screen.queryByText(ENDED), null);
});

test("A3: a normal sign-out leaves no sign-in-ended line", async () => {
    // A normal sign-out never writes the marker (only the expired handler does).
    localStorage.setItem("talky.logout.pending", "1");
    await visit();
    assert.ok(await screen.findByText(UNCONFIRMED));
    assert.equal(screen.queryByText(ENDED), null);
});

test("A4: with both triggers only the logout-unconfirmed line shows", async () => {
    sessionStorage.setItem(SIGNIN_ENDED_FLAG, String(Date.now()));
    await visit("?logout=unconfirmed");
    assert.ok(await screen.findByText(UNCONFIRMED));
    assert.equal(screen.queryByText(ENDED), null);
});

test("A5: the marker is cleared once shown; a second visit shows nothing", async () => {
    sessionStorage.setItem(SIGNIN_ENDED_FLAG, String(Date.now()));
    await visit();
    assert.ok(await screen.findByText(ENDED));
    assert.equal(sessionStorage.getItem(SIGNIN_ENDED_FLAG), null);
    cleanup();
    await visit();
    assert.equal(screen.queryByText(ENDED), null);
});

test("A6: a marker nobody read for a long time is ignored", async () => {
    sessionStorage.setItem(SIGNIN_ENDED_FLAG, String(Date.now() - 60 * 60 * 1000));
    await visit();
    assert.equal(screen.queryByText(ENDED), null);
});
