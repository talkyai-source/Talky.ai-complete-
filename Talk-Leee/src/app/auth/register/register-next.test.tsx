import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, mock, test } from "node:test";
import { fileURLToPath } from "node:url";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { api, type MeResponse } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
import { AuthProvider } from "@/lib/auth-context";
import { postAuthDashboard } from "@/lib/post-auth-navigation";
import RegisterClientPage from "./register-client";
import { destinationAfterSignup, signInHref, signUpHref } from "./register-next";
import { ASKAI_SIGNIN_FLAG } from "@/components/auth/askai-signin-notice";

// W2-T11 to T14: the sign-up page follows a safe `next` and ignores every unsafe one.
const here = path.dirname(fileURLToPath(import.meta.url));
const registerSource = readFileSync(path.join(here, "register-client.tsx"), "utf8");
const loginSource = readFileSync(path.join(here, "..", "login", "login-client.tsx"), "utf8");

afterEach(() => { cleanup(); mock.restoreAll(); localStorage.clear(); sessionStorage.removeItem(ASKAI_SIGNIN_FLAG); });

const SAFE = "/billing/plans/?interval=year";
// Each value is what the page's address holds after the browser decoded the query once.
const UNSAFE: [string, string][] = [
    ["another site", "https://evil.example"],
    ["a protocol-relative address", "//evil.example"],
    ["a slash and a backslash", "/\\evil.example"],
    ["a javascript: address", "javascript:alert(1)"],
    ["a backslash inside the path", "/billing\\..\\evil"],
    ["a control character", "/billing/\u0001plans"],
    ["a newline", "/billing/\nplans"],
    ["an encoded protocol-relative address", "%2F%2Fevil.example"],
    ["an encoded slash pair after a slash", "/%2F%2Fevil.example"],
    ["an encoded backslash", "/%5Cevil.example"],
    ["a sign-in page", "/auth/login"],
    ["a sign-up page", "/auth/register?next=/billing/"],
];

function mount(next: string | null, signedIn: { role: string } | false, onNavigate?: (url: string) => void) {
    if (signedIn) localStorage.setItem("talklee.auth.token", "dummy");
    mock.method(api, "getMe", async () => {
        if (!signedIn) throw new ApiClientError({ status: 401, code: "unauthorized", message: "Sign in", url: "/auth/me", method: "GET" });
        return { id: "user-1", email: "owner@example.test", role: signedIn.role, tenant_id: "tenant-1", minutes_remaining: 0 } as MeResponse;
    });
    const replaced: string[] = [];
    const router = { push() {}, replace: (url: string) => { replaced.push(url); }, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
    const params = new URLSearchParams();
    if (next !== null) params.set("next", next);
    const view = render(
        <AppRouterContext.Provider value={router}>
            <SearchParamsContext.Provider value={params}>
                <AuthProvider><RegisterClientPage {...(onNavigate ? { onNavigate } : {})} /></AuthProvider>
            </SearchParamsContext.Provider>
        </AppRouterContext.Provider>,
    );
    return { ...view, replaced };
}

test("W2-T13 a signed-in visitor is not shown the form and is sent to a safe next", async () => {
    const { replaced } = mount(SAFE, { role: "tenant_admin" });
    await waitFor(() => assert.deepEqual(replaced, [SAFE]));
    assert.equal(screen.queryByLabelText("Work Email"), null);
    assert.equal(screen.queryByRole("heading", { name: "Create your account" }), null);
});

test("W2-T13 a signed-in visitor with no next goes to the dashboard; a white-label admin to their own", async () => {
    const plain = mount(null, { role: "tenant_admin" });
    await waitFor(() => assert.deepEqual(plain.replaced, ["/dashboard"]));
    cleanup();
    const white = mount(SAFE, { role: "white_label_admin" });
    await waitFor(() => assert.deepEqual(white.replaced, ["/white-label/dashboard"]));
});

for (const [name, value] of UNSAFE) {
    test(`W2-T12 a signed-in visitor with ${name} as next lands on the dashboard`, async () => {
        const { replaced } = mount(value, { role: "tenant_admin" });
        await waitFor(() => assert.deepEqual(replaced, ["/dashboard"]));
        assert.equal(postAuthDashboard("tenant_admin", value), "/dashboard", "the helper the completion step calls");
        assert.equal(signInHref(value), "/auth/login", "the sign-in link does not carry it");
    });
}

test("W2-T11 a visitor who is not signed in sees the form; its sign-in link keeps a safe next", async () => {
    const { replaced } = mount(SAFE, false);
    assert.ok(await screen.findByRole("heading", { name: "Create your account" }));
    assert.equal(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"), `/auth/login?next=${encodeURIComponent(SAFE)}`, "next/link drops the trailing slash from the rendered href (trailingSlash is off)");
    assert.equal(signInHref(SAFE), `/auth/login/?next=${encodeURIComponent(SAFE)}`, "the address the page builds");
    assert.deepEqual(replaced, []);
});

test("W2-T12 a visitor who is not signed in, with an unsafe next, gets the plain sign-in link", async () => {
    mount("https://evil.example", false);
    assert.ok(await screen.findByRole("heading", { name: "Create your account" }));
    assert.equal(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"), "/auth/login");
});

test("W2-T11 the account-created step gets its destination from the pure function; the hard navigation stays", () => {
    assert.match(registerSource, /destinationAfterSignup\(response\.role, next, fromAskAi\)/);
    assert.match(registerSource, /onNavigate\(destination\)/);
    assert.doesNotMatch(registerSource, /import \{[^}]*safeInternalPath|new URL\(|startsWith\("\/"/, "no check of its own: the shared helper only");
});

test("W2-T13 the sign-in page sends no signed-in visitor to sign-up, so the two pages cannot loop", () => {
    assert.doesNotMatch(loginSource, /(router\.(push|replace)|location\.(assign|replace))\s*\(\s*["'`]\/auth\/register/);
});

test("W2-T14 the Ask AI arrival and the white-label role keep their rule at the completion step", () => {
    assert.match(registerSource, /hasAskAiSignInFlag\(\)/);
    assert.equal(postAuthDashboard("white_label_admin", SAFE), "/white-label/dashboard");
});

test("M2 destinationAfterSignup: a safe next, every unsafe next, white-label, Ask AI", () => {
    assert.equal(destinationAfterSignup("tenant_admin", SAFE, false), SAFE);
    for (const [name, value] of UNSAFE) assert.equal(destinationAfterSignup("tenant_admin", value, false), "/dashboard", name);
    assert.equal(destinationAfterSignup("tenant_admin", null, false), "/dashboard");
    assert.equal(destinationAfterSignup("white_label_admin", SAFE, false), "/white-label/dashboard");
    assert.equal(destinationAfterSignup("tenant_admin", SAFE, true), "/", "Ask AI wins over a safe next");
    assert.equal(destinationAfterSignup("tenant_admin", "https://evil.example", true), "/");
    assert.equal(destinationAfterSignup("white_label_admin", SAFE, true), "/white-label/dashboard", "white-label keeps its own dashboard");
});

async function completeSignup(next: string | null, role: string, navigated: string[]) {
    const email = "owner@example.com";
    const client = api;
    mock.method(client, "signupStart", async () => ({ email, message: "Code sent", expires_in_minutes: 10 }));
    mock.method(client, "signupVerifyCode", async () => ({ email, message: "Verified" }));
    mock.method(client, "signupComplete", async () => ({
        access_token: "dummy", user_id: "user-1", tenant_id: "tenant-1", email, role,
        business_name: "Example Company", minutes_remaining: 0, minutes_state: "ok",
    }) as never);
    const view = mount(next, false, (url) => { navigated.push(url); });
    fireEvent.change(await screen.findByLabelText("Your Name"), { target: { value: "Person" } });
    fireEvent.change(screen.getByLabelText("Business Name"), { target: { value: "Example Company" } });
    fireEvent.change(screen.getByLabelText("Work Email"), { target: { value: email } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    fireEvent.change(await screen.findByLabelText("Verification code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    const password = "a long enough passphrase for tests";
    fireEvent.change(await screen.findByLabelText("Password"), { target: { value: password } });
    fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: password } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => assert.equal(navigated.length, 1));
    // Let any effect that the new signed-in state triggers run before the caller looks at the router.
    await new Promise((resolve) => setTimeout(resolve, 20));
    return view;
}

test("M2 after the account is created here, router.replace is never called and the hard navigation gets the safe next", async () => {
    const navigated: string[] = [];
    const { replaced } = await completeSignup(SAFE, "tenant_admin", navigated);
    assert.deepEqual(navigated, [SAFE]);
    assert.deepEqual(replaced, [], "the signed-in redirect must not run beside the hard navigation (createdHere guard)");
});

test("M2 an unsafe next, a white-label role and an Ask AI arrival reach the hard navigation as the pure function says", async () => {
    const unsafe: string[] = [];
    const first = await completeSignup("//evil.example", "tenant_admin", unsafe);
    assert.deepEqual(unsafe, ["/dashboard"]);
    assert.deepEqual(first.replaced, []);
    cleanup(); mock.restoreAll(); localStorage.clear();
    const white: string[] = [];
    await completeSignup(SAFE, "white_label_admin", white);
    assert.deepEqual(white, ["/white-label/dashboard"]);
    cleanup(); mock.restoreAll(); localStorage.clear();
    sessionStorage.setItem(ASKAI_SIGNIN_FLAG, "1");
    const askAi: string[] = [];
    await completeSignup(SAFE, "tenant_admin", askAi);
    assert.deepEqual(askAi, ["/"]);
});

test("the sign-up link of the sign-in page keeps a safe next, in the same trailing-slash form as signInHref", () => {
    assert.equal(signUpHref(SAFE), `/auth/register/?next=${encodeURIComponent(SAFE)}`);
    assert.equal(signUpHref(null), "/auth/register/");
});

for (const [name, value] of UNSAFE) {
    test(`the sign-up link of the sign-in page does not carry ${name}`, () => {
        assert.equal(signUpHref(value), "/auth/register/");
    });
}
