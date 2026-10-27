import React from "react";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext, type AppRouterInstance } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import RegisterPage from "./register/register-client";
import ForgotPasswordPage from "./forgot-password/page";
import CallbackPage from "./callback/page";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api, type MeResponse } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace } from "@/lib/http-client";

const router = { back() {}, forward() {}, refresh() {}, push() {}, replace() {}, prefetch() {}, bfcacheId: "test" } satisfies AppRouterInstance;
const startResponse = { email: "person@example.test", message: "Verification code sent!", expires_in_minutes: 10 };
const originalBearerFallback = process.env.NEXT_PUBLIC_BEARER_FALLBACK;
const anonymousSession = new ApiClientError({ status: 401, code: "unauthorized", message: "No browser session.", url: "/auth/me", method: "GET" });

beforeEach(() => {
    localStorage.clear();
    clearFreshLoginGrace();
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "true";
    window.history.replaceState({}, "", "/auth/register");
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected network request in auth journey test"); });
});
afterEach(() => {
    cleanup(); mock.restoreAll(); localStorage.clear();
    clearFreshLoginGrace();
    if (originalBearerFallback === undefined) delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    else process.env.NEXT_PUBLIC_BEARER_FALLBACK = originalBearerFallback;
});

function renderPage(page: React.ReactNode, query = "") {
    return render(<AppRouterContext.Provider value={router}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>
            <AuthProvider>{page}</AuthProvider>
        </SearchParamsContext.Provider>
    </AppRouterContext.Provider>);
}
function input(label: string | RegExp, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
function startRegistration() {
    input("Your Name", "Person");
    input("Business Name", "Example Company");
    input("Work Email", "Person@example.test");
    fireEvent.click(screen.getByRole("button", { name: "Get Started" }));
}

test("failed delivery and duplicate signup stay on the account form and permit retry", async () => {
    let attempts = 0;
    mock.method(api, "signupStart", async () => {
        attempts++;
        if (attempts === 1) throw new Error("Could not send a verification code. Please try again.");
        if (attempts === 2) throw new Error("You are already registered. Please sign in or try another email address.");
        return startResponse;
    });
    renderPage(<RegisterPage />);
    startRegistration();
    assert.match((await screen.findByRole("alert")).textContent!, /Could not send/);
    assert.equal(screen.queryByLabelText("Verification Code"), null);
    fireEvent.click(screen.getByRole("button", { name: "Get Started" }));
    await waitFor(() => assert.match(screen.getByRole("alert").textContent!, /already registered/));
    assert.equal(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"), "/auth/login");
    fireEvent.click(screen.getByRole("button", { name: "Get Started" }));
    await screen.findByLabelText("Verification Code");
    assert.match(screen.getByText(/Enter the verification code sent to/).textContent!, /person@example.test/);
});

test("expired signup code remains recoverable and successful resend clears the old code", async () => {
    mock.method(api, "signupStart", async () => startResponse);
    const verify = mock.method(api, "signupVerifyCode", async () => { throw new Error("Invalid or expired verification code."); });
    renderPage(<RegisterPage />);
    startRegistration();
    await screen.findByLabelText("Verification Code");
    input("Verification Code", "123");
    assert.equal((screen.getByRole("button", { name: "Next" }) as HTMLButtonElement).disabled, true);
    input("Verification Code", "123456");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    assert.match((await screen.findByRole("alert")).textContent!, /expired/);
    assert.deepEqual(verify.mock.calls[0]?.arguments, ["person@example.test", "123456"]);
    fireEvent.click(screen.getByRole("button", { name: /Resend/ }));
    await waitFor(() => assert.equal((screen.getByLabelText("Verification Code") as HTMLInputElement).value, ""));
    assert.equal(screen.queryByLabelText("Password"), null);
});

test("signup completion expiry lets the user return and request a new code without losing their account details", async () => {
    mock.method(api, "signupStart", async () => startResponse);
    mock.method(api, "signupVerifyCode", async () => ({ email: startResponse.email, message: "Verified" }));
    mock.method(api, "signupComplete", async () => { throw new Error("Signup session expired. Please start again."); });
    renderPage(<RegisterPage />);
    startRegistration();
    await screen.findByLabelText("Verification Code");
    input("Verification Code", "123456");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByLabelText("Password");
    input("Password", "a secure password");
    input("Confirm Password", "a secure password");
    fireEvent.click(screen.getByRole("button", { name: "Create Account" }));
    assert.match((await screen.findByRole("alert")).textContent!, /session expired/);
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    fireEvent.click(screen.getByRole("button", { name: /Resend/ }));
    await waitFor(() => assert.equal((screen.getByLabelText("Verification Code") as HTMLInputElement).value, ""));
    assert.match(screen.getByText(/Enter the verification code sent to/).textContent!, /person@example.test/);
});

test("recovery uses honest request wording and permits a new code after expiry", async () => {
    const calls: { path: string; body: unknown }[] = [];
    mock.method(api, "request", async (options: { path: string; body: unknown }) => {
        calls.push(options);
        if (options.path.endsWith("reset-password")) throw new Error("Code expired. Please request a new code.");
        return { message: "If an account exists, a reset code has been requested." };
    });
    renderPage(<ForgotPasswordPage />, "email=Person%40example.test");
    fireEvent.click(screen.getByRole("button", { name: "Send Reset Code" }));
    await screen.findByLabelText("Code");
    assert.match(screen.getByRole("status").textContent!, /If this email is registered/);
    assert.equal(screen.queryByText(/code we sent/), null);
    input("Code", "123"); input("New password", "a secure password"); input("Confirm new password", "a secure password");
    fireEvent.click(screen.getByRole("button", { name: "Reset Password" }));
    assert.match((await screen.findByRole("alert")).textContent!, /6-digit/);
    assert.equal(calls.length, 1);
    input("Code", "123456");
    fireEvent.click(screen.getByRole("button", { name: "Reset Password" }));
    assert.match((await screen.findByRole("alert")).textContent!, /expired/);
    fireEvent.click(screen.getByRole("button", { name: "Request a new code" }));
    await waitFor(() => assert.equal((screen.getByLabelText("Code") as HTMLInputElement).value, ""));
    assert.equal(calls.length, 3);
    assert.deepEqual(calls[0]?.body, { email: "person@example.test" });
    assert.deepEqual(calls[2]?.body, { email: "person@example.test" });
});

test("recovery request failure stays on email form with a retry", async () => {
    mock.method(api, "request", async () => { throw new Error("Service unavailable. Please try again."); });
    renderPage(<ForgotPasswordPage />, "email=person%40example.test");
    fireEvent.click(screen.getByRole("button", { name: "Send Reset Code" }));
    assert.match((await screen.findByRole("alert")).textContent!, /Service unavailable/);
    assert.equal(screen.queryByLabelText("Code"), null);
    assert.equal((screen.getByRole("button", { name: "Send Reset Code" }) as HTMLButtonElement).disabled, false);
});

test("callback type alone cannot confirm an account or create a profile", async () => {
    window.history.replaceState({}, "", "/auth/callback?type=signup");
    const me = mock.method(api, "getMe", async () => { throw anonymousSession; });
    const request = mock.method(api, "request", async () => { throw new Error("Unexpected profile creation"); });
    renderPage(<CallbackPage />);
    await screen.findByText(/This link could not confirm your account/);
    // The provider checks the existing cookie session; the link verifies no token.
    assert.deepEqual(me.mock.calls.map(call => call.arguments), [[]]);
    assert.equal(request.mock.callCount(), 0);
    assert.equal(localStorage.getItem("talklee.auth.token"), null);
    assert.equal(window.location.search, "");
});

test("callback rejection verifies exact token without persisting it or claiming success", async () => {
    window.history.replaceState({}, "", "/auth/callback#access_token=invalid-token");
    const me = mock.method(api, "getMe", async (token?: string) => {
        if (token === undefined) throw anonymousSession;
        throw new Error("This token is expired.");
    });
    const setToken = mock.method(api, "setToken", () => {});
    renderPage(<CallbackPage />);
    await screen.findByText("This token is expired.");
    assert.deepEqual(me.mock.calls.map(call => call.arguments), [[], ["invalid-token"]]);
    assert.equal(setToken.mock.callCount(), 0);
    assert.equal(localStorage.getItem("talklee.auth.token"), null);
    assert.equal(window.location.hash, "");
});

test("callback result cannot replace an identity established while verification was pending", async () => {
    window.history.replaceState({}, "", "/auth/callback#access_token=old-link");
    let resolve!: (value: MeResponse) => void;
    const me = mock.method(api, "getMe", async (token?: string) => {
        if (token === undefined) throw anonymousSession;
        assert.equal(token, "old-link");
        return new Promise<MeResponse>((done) => { resolve = done; });
    });
    function OtherSignIn() {
        const auth = useAuth();
        return <><button onClick={() => auth.applyLoginResult({ user_id: "new-user", email: "new@example.test", role: "user", access_token: "new-session" })}>Other sign in</button><output>{auth.user?.id}</output></>;
    }
    renderPage(<><CallbackPage /><OtherSignIn /></>);
    await waitFor(() => assert.deepEqual(me.mock.calls.map(call => call.arguments), [[], ["old-link"]]));
    fireEvent.click(screen.getByRole("button", { name: "Other sign in" }));
    await act(async () => resolve({ id: "old-user", email: "old@example.test", role: "user", minutes_remaining: 0 } as MeResponse));
    await screen.findByText(/Your sign-in changed/);
    assert.equal(screen.getByRole("status").textContent, "new-user");
    assert.equal(localStorage.getItem("talklee.auth.token"), "new-session");
});

test("cookie-only callback requires normal sign-in and preserves the existing account", async () => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    localStorage.setItem("talklee.auth.token", "existing-session");
    window.history.replaceState({}, "", "/auth/callback#access_token=other-account-link");
    const me = mock.method(api, "getMe", async () => ({
        id: "existing-user", email: "existing@example.test", role: "user", minutes_remaining: 10,
    } as MeResponse));
    const setToken = mock.method(api, "setToken", () => {});
    const push = mock.method(router, "push", () => {});
    function Identity() {
        const auth = useAuth();
        return <output>{`${auth.user?.id}:${auth.accessToken}`}</output>;
    }
    renderPage(<><CallbackPage /><Identity /></>);
    await screen.findByText(/Please sign in with your email and password to continue/);
    assert.equal(screen.getByRole("status").textContent, "existing-user:existing-session");
    assert.equal(setToken.mock.callCount(), 0);
    assert.equal(me.mock.callCount(), 1);
    assert.deepEqual(me.mock.calls[0]?.arguments, []); // Only the existing session's bootstrap.
    assert.equal(window.location.hash, "");
    assert.equal(window.location.pathname, "/auth/callback");
    fireEvent.click(screen.getByRole("button", { name: "Try Again" }));
    assert.deepEqual(push.mock.calls[0]?.arguments, ["/auth/login"]);
});
