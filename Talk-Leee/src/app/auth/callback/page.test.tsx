import { test, afterEach, describe } from "node:test";
import assert from "node:assert/strict";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import AuthCallbackPage from "./page";
import { AuthProvider } from "@/lib/auth-context";

/**
 * QA F4-09 (QA-SEC-042): /auth/callback signed the visitor in with any
 * access_token placed in the URL, so an attacker's link could sign a victim
 * into the attacker's account (login CSRF). No backend flow links here with a
 * token, so the URL-token path is gone: the page cleans the URL and asks for a
 * normal sign-in, and never verifies or applies a token from the URL.
 */
const originalFetch = global.fetch;

function renderAt(url: string) {
    window.history.replaceState({}, "", url);
    const router = {
        push: () => {}, replace: () => {}, refresh: () => {}, back: () => {}, forward: () => {},
        prefetch: async () => {},
    } as unknown as React.ContextType<typeof AppRouterContext>;
    return render(
        <AppRouterContext.Provider value={router}>
            <AuthProvider>
                <AuthCallbackPage />
            </AuthProvider>
        </AppRouterContext.Provider>,
    );
}

describe("AuthCallbackPage", () => {
    afterEach(() => {
        cleanup();
        global.fetch = originalFetch;
    });

    for (const url of ["/auth/callback#access_token=attacker-token", "/auth/callback?access_token=attacker-token"]) {
        test(`a token in the URL never signs anyone in (${url.includes("#") ? "hash" : "query"})`, async () => {
            // The auth provider's own session check may run; what must never
            // happen is a request that carries the token taken from the URL.
            const requested: string[] = [];
            global.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
                requested.push(`${String(input)} ${JSON.stringify(init?.headers ?? {})}`);
                return new Response(JSON.stringify({ detail: "unexpected" }), { status: 401 });
            }) as typeof fetch;

            renderAt(url);
            await waitFor(() => assert.ok(screen.getByText(/Please sign in/i)));
            assert.ok(!requested.some((request) => request.includes("attacker-token")),
                `the URL token was sent to the API: ${requested.join(" | ")}`);
            assert.ok(!window.location.href.includes("attacker-token"), "the token stays in the address bar");
        });
    }

    test("a provider error in the URL is still shown", async () => {
        global.fetch = (async () => new Response("{}", { status: 401 })) as typeof fetch;
        renderAt("/auth/callback#error=access_denied&error_description=Access%20was%20denied");
        await waitFor(() => assert.ok(screen.getByText("Access was denied")));
    });
});
