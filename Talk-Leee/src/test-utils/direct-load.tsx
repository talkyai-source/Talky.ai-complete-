/**
 * FX1a Task 1 — harness for "direct load / refresh" page tests.
 *
 * Mounts a page under the REAL AuthProvider and the REAL shared HTTP client
 * while the first sign-in check (/auth/me) is still pending, exactly as a
 * direct load does: the page asks for its data before the check has answered.
 * Only `fetch` and `api.getMe` are replaced.
 */
import assert from "node:assert/strict";
import React, { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";

import { api } from "@/lib/api";
import { AuthProvider } from "@/lib/auth-context";
import { clearFreshLoginGrace, setTokenProvider } from "@/lib/http-client";

export const SIGN_IN_WAIT_MESSAGE = /Please wait while your sign-in is checked/;

const ME = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;

const originalGetMe = api.getMe;
const originalFetch = globalThis.fetch;
let client: QueryClient | null = null;

export function restoreDirectLoad() {
    cleanup();
    client?.clear();
    client = null;
    api.getMe = originalGetMe;
    globalThis.fetch = originalFetch;
    setTokenProvider(null);
    clearFreshLoginGrace();
    window.localStorage.clear();
    window.sessionStorage.clear();
}

/** Mounts its children one commit after the provider, like a page segment
 *  that hydrates after the app shell has started the sign-in check. */
function AfterShell({ children }: { children: React.ReactNode }) {
    const [ready, setReady] = useState(false);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- test-only: reproduces the page mounting after the provider's effects
    useEffect(() => { setReady(true); }, []);
    return ready ? <>{children}</> : null;
}

export type DirectLoadRoutes = Array<[pathEnd: RegExp, body: unknown]>;

/**
 * Renders `page` while the sign-in check is pending, proves nothing failed and
 * nothing was sent, then lets the check verify the user and waits for
 * `dataShown` — with no click in between.
 */
export async function expectDirectLoadShowsData(page: React.ReactNode, routes: DirectLoadRoutes, dataShown: () => boolean) {
    const sent: string[] = [];
    globalThis.fetch = (async (input) => {
        const url = new URL(String(input));
        sent.push(url.pathname);
        const match = routes.find(([pathEnd]) => pathEnd.test(url.pathname));
        return match ? Response.json(match[1]) : Response.json({ detail: `unrouted ${url.pathname}` }, { status: 404 });
    }) as typeof fetch;
    let verify!: () => void;
    const answered = new Promise<typeof ME>((resolve) => { verify = () => resolve({ ...ME }); });
    api.getMe = () => answered;
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });

    render(
        <AuthProvider>
            <QueryClientProvider client={client}>
                <AfterShell>{page}</AfterShell>
            </QueryClientProvider>
        </AuthProvider>,
    );
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
    assert.equal(screen.queryByText(SIGN_IN_WAIT_MESSAGE) === null, true, "no sign-in error while the check is pending");
    assert.equal(screen.queryAllByRole("alert").length, 0, "no error of any kind while the check is pending");
    assert.equal(sent.length, 0, "nothing is sent before the sign-in check has answered");

    await act(async () => { verify(); await answered; });
    await waitFor(() => assert.equal(dataShown(), true, "the page shows its data without any click"), { timeout: 8_000 });
    assert.equal(screen.queryByText(SIGN_IN_WAIT_MESSAGE) === null, true);
    return sent;
}
