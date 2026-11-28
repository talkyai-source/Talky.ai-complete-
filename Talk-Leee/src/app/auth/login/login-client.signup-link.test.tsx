import React from "react";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
import LoginClientPage from "./login-client";

// The sign-in page's "Create an account" link keeps a safe `next`, so a visitor who came from a
// plan page, chose "Sign in" and then "Create an account" does not lose where they were going.
const originalGetMe = api.getMe;
let client: QueryClient | undefined;
afterEach(() => { cleanup(); client?.clear(); client = undefined; api.getMe = originalGetMe; api.clearToken(); localStorage.clear(); sessionStorage.clear(); });

async function signUpLink(next: string | undefined): Promise<string | null> {
    api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "anonymous", url: "/auth/me", method: "GET" }); };
    const query = new URLSearchParams(next === undefined ? {} : { next });
    const router = { bfcacheId: "signup-link", push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } satisfies NonNullable<React.ContextType<typeof AppRouterContext>>;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    render(<AppRouterContext.Provider value={router}><PathnameContext.Provider value="/auth/login">
        <SearchParamsContext.Provider value={query}><QueryClientProvider client={client}>
            <AuthProvider><LoginClientPage /></AuthProvider>
        </QueryClientProvider></SearchParamsContext.Provider>
    </PathnameContext.Provider></AppRouterContext.Provider>);
    const link = await screen.findByRole("link", { name: "Create an account" });
    await waitFor(() => assert.ok(link.getAttribute("href")));
    return link.getAttribute("href");
}

const SAFE = "/billing/plans/?interval=year";

test("with a safe next the sign-up link carries it", async () => {
    // next/link drops the trailing slash from the rendered href (trailingSlash is off), as on the sign-up page.
    assert.equal(await signUpLink(SAFE), `/auth/register?next=${encodeURIComponent(SAFE)}`);
});

for (const bad of ["https://evil.example", "//x", "/\\x","javascript:alert(1)", "/auth/login", "/auth/register?next=/billing/"]) {
    test(`with ${JSON.stringify(bad)} as next the sign-up link is plain`, async () => {
        assert.equal(await signUpLink(bad), "/auth/register");
    });
}

test("without a next the sign-up link is plain", async () => {
    assert.equal(await signUpLink(undefined), "/auth/register");
});
