/**
 * FX6c Task 5 (plan 3.6: "Do not leave a suspended customer on a generic
 * broken/401 experience."). A sign-in refused because the account is on hold
 * (403 tenant_suspended, backend/app/core/security/principal.py:22-34) shows
 * the server's own message on the sign-in page, for password sign-in and for
 * passkey sign-in. The refusal below is the error the HTTP client builds from
 * the server's envelope {error: {code, message, details: {reason}}}.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import PasskeyLogin from "@/components/auth/passkey-login";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import LoginClientPage from "./login-client";

const HOLD_MESSAGE = "This account is on hold, so sign-in is paused. Contact support.";
const holdRefusal = (url: string) => new ApiClientError({
    status: 403, code: "tenant_suspended", message: HOLD_MESSAGE, details: { reason: "tenant_suspended" }, url, method: "POST",
});

const originalGetMe = api.getMe, originalLogin = api.login;
const originalBegin = api.beginPasskeyLogin, originalComplete = api.completePasskeyLogin;
const originalFetch = globalThis.fetch;
const savedDescriptors = [
    [navigator, "credentials"], [window, "PublicKeyCredential"], [globalThis, "PublicKeyCredential"],
].map(([target, key]) => ({ target: target as object, key: key as string, descriptor: Object.getOwnPropertyDescriptor(target, key as string) }));
let client: QueryClient | null = null;

afterEach(() => {
    cleanup(); client?.clear(); client = null;
    api.getMe = originalGetMe; api.login = originalLogin; api.beginPasskeyLogin = originalBegin; api.completePasskeyLogin = originalComplete;
    globalThis.fetch = originalFetch;
    for (const { target, key, descriptor } of savedDescriptors) {
        if (descriptor) Object.defineProperty(target, key, descriptor); else Reflect.deleteProperty(target, key);
    }
    localStorage.clear(); clearFreshLoginGrace(); invalidateRequestIdentity();
});

test("password sign-in refused for a hold: the server's message shows on the sign-in page", async () => {
    localStorage.clear(); clearFreshLoginGrace();
    api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "anonymous", url: "/auth/me", method: "GET" }); };
    api.login = async () => { throw holdRefusal("/auth/login"); };
    globalThis.fetch = async () => { throw new Error("no network in this test"); };
    const router = { bfcacheId: "synthetic", push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    render(
        <AppRouterContext.Provider value={router}><PathnameContext.Provider value="/auth/login">
            <QueryClientProvider client={client}><AuthProvider><LoginClientPage /></AuthProvider></QueryClientProvider>
        </PathnameContext.Provider></AppRouterContext.Provider>,
    );
    const email = await screen.findByLabelText("Email", {}, { timeout: 3000 });
    fireEvent.change(email, { target: { value: "a@example.test" } });
    fireEvent.submit(email.closest("form")!);
    const password = await screen.findByLabelText("Password", {}, { timeout: 3000 });
    fireEvent.change(password, { target: { value: "synthetic-password" } });
    await act(async () => { fireEvent.submit(password.closest("form")!); });
    await waitFor(() => assert.equal(screen.queryAllByRole("alert").some((node) => node.textContent === HOLD_MESSAGE), true));
    assert.equal(document.body.textContent?.includes("Something went wrong"), false, "not a generic error");
    assert.equal(screen.queryByLabelText("Password") !== null, true, "the customer stays on the sign-in page");
});

test("passkey sign-in refused for a hold: the server's message shows", async () => {
    class SyntheticCredential {
        id = "key"; rawId = new Uint8Array([1]).buffer; type = "public-key";
        response = { clientDataJSON: new Uint8Array([2]).buffer, authenticatorData: new Uint8Array([3]).buffer, signature: new Uint8Array([4]).buffer, userHandle: null };
    }
    Object.defineProperty(window, "PublicKeyCredential", { configurable: true, value: SyntheticCredential });
    Object.defineProperty(globalThis, "PublicKeyCredential", { configurable: true, value: SyntheticCredential });
    Object.defineProperty(navigator, "credentials", { configurable: true, value: { create: async () => new SyntheticCredential(), get: async () => new SyntheticCredential() } });
    api.beginPasskeyLogin = (async () => ({ ceremony_id: "c", has_passkeys: true, options: { challenge: "AQI", rpId: "example.test", userVerification: "required", timeout: 42000 } })) as unknown as typeof api.beginPasskeyLogin;
    api.completePasskeyLogin = async () => { throw holdRefusal("/auth/passkeys/login/complete"); };
    const errors: string[] = [];
    render(<PasskeyLogin onSuccess={() => { errors.push("signed in"); }} onError={(error) => { errors.push(error); }} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Sign in with Passkey/i })); });
    await waitFor(() => assert.deepEqual(errors, [HOLD_MESSAGE]), { timeout: 3000 });
    assert.equal(document.body.textContent?.includes(HOLD_MESSAGE), true, "the message is on the page");
});
