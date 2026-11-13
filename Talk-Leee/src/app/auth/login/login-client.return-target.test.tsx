import React, { useLayoutEffect } from "react";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { getBrowserAuthToken } from "@/lib/auth-token";
import { ApiClientError, clearFreshLoginGrace } from "@/lib/http-client";
import { ASKAI_SIGNIN_FLAG } from "@/components/auth/askai-signin-notice";
import LoginClientPage from "./login-client";

const originalGetMe = api.getMe, originalLogin = api.login;
const originalWindow = window, originalFetch = globalThis.fetch;
const response: Awaited<ReturnType<typeof api.login>> = {
    access_token: "synthetic-return-target-token", token_type: "bearer", user_id: "user-return-target",
    tenant_id: "tenant-return-target", email: "person@example.test", role: "tenant_admin",
    minutes_remaining: 1, minutes_state: "known", message: "", mfa_required: false,
    mfa_challenge_token: undefined, business_name: undefined,
};
let auth: ReturnType<typeof useAuth>;
let client: QueryClient | undefined;
function Identity() {
    const current = useAuth();
    useLayoutEffect(() => { auth = current; });
    return <span data-testid="identity">{current.user?.id ?? "anonymous"}</span>;
}
afterEach(() => {
    Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
    cleanup(); client?.clear(); client = undefined;
    api.getMe = originalGetMe; api.login = originalLogin; globalThis.fetch = originalFetch;
    api.clearToken(); localStorage.clear(); sessionStorage.clear(); clearFreshLoginGrace();
    originalWindow.history.replaceState({}, "", "/");
});

async function completeLogin(next: string | undefined, role = "tenant_admin", askAi = false) {
    localStorage.clear(); sessionStorage.clear(); api.clearToken(); clearFreshLoginGrace();
    const query = new URLSearchParams(next === undefined ? {} : { next });
    originalWindow.history.replaceState({}, "", `/auth/login?${query}`);
    if (askAi) sessionStorage.setItem(ASKAI_SIGNIN_FLAG, "1");
    api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "anonymous", url: "/auth/me", method: "GET" }); };
    let loginCalls = 0;
    api.login = async () => { loginCalls++; return { ...response, role }; };
    globalThis.fetch = async () => { throw new Error("No provider or network request allowed in return-target test"); };
    const soft: string[] = [];
    const hard: { destination: string; identity: string | undefined; token: string | null; askAiFlag: string | null }[] = [];
    const router = { bfcacheId: "return-target", push: (path: string) => { soft.push(path); },
        replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } satisfies NonNullable<React.ContextType<typeof AppRouterContext>>;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    render(<AppRouterContext.Provider value={router}><PathnameContext.Provider value="/auth/login">
        <SearchParamsContext.Provider value={query}><QueryClientProvider client={client}>
            <AuthProvider><Identity /><LoginClientPage /></AuthProvider>
        </QueryClientProvider></SearchParamsContext.Provider>
    </PathnameContext.Provider></AppRouterContext.Provider>);
    await waitFor(() => assert.equal(auth.loading, false));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: response.email } });
    fireEvent.submit(screen.getByLabelText("Email").closest("form")!);
    const password = await screen.findByLabelText("Password", {}, { timeout: 3000 });
    fireEvent.change(password, { target: { value: "synthetic-password" } });
    const location = { origin: originalWindow.location.origin, href: originalWindow.location.href,
        search: originalWindow.location.search, pathname: "/auth/login",
        assign: (destination: string) => { hard.push({ destination, identity: auth.user?.id,
            token: getBrowserAuthToken(), askAiFlag: sessionStorage.getItem(ASKAI_SIGNIN_FLAG) }); } };
    Object.defineProperty(globalThis, "window", { configurable: true, value: new Proxy(originalWindow, {
        get(target, key) { return key === "location" ? location : Reflect.get(target, key); },
    }) });
    await act(async () => { fireEvent.submit(password.closest("form")!); });
    await waitFor(() => assert.equal(hard.length, 1));
    assert.equal(loginCalls, 1);
    assert.deepEqual(soft, [], "Successful login must keep the hard-navigation cookie boundary");
    assert.equal(hard[0]!.identity, response.user_id, "AuthContext must be hydrated before navigation");
    assert.equal(hard[0]!.token, response.access_token);
    assert.equal(hard[0]!.askAiFlag, null);
    return hard[0]!.destination;
}

test("actual login returns a signed-out billing visitor to the original deep link", async () => {
    assert.equal(await completeLogin("/billing/"), "/billing/");
});
test("actual login preserves the original query and fragment", async () => {
    assert.equal(await completeLogin("/calls?search=Alice%20Smith#recent"), "/calls?search=Alice%20Smith#recent");
});
test("actual login rejects an external return target", async () => {
    assert.equal(await completeLogin("https://evil.example/path"), "/dashboard");
});
test("actual login rejects an encoded auth-loop target", async () => {
    assert.equal(await completeLogin("/%61uth/login?next=/billing"), "/dashboard");
});
test("homepage Ask AI flag still wins over the return URL and clears on completion", async () => {
    assert.equal(await completeLogin("/billing/", "tenant_admin", true), "/");
});
test("white-label login keeps its own dashboard even with Ask AI and a return URL", async () => {
    assert.equal(await completeLogin("/billing/", "white_label_admin", true), "/white-label/dashboard");
});
test("ordinary login without a return target retains the dashboard", async () => {
    assert.equal(await completeLogin(undefined), "/dashboard");
});
