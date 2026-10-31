import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { useEffect } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace, createHttpClient } from "@/lib/http-client";

const originalGetMe = api.getMe;
const originalFetch = globalThis.fetch;
const originalFallback = process.env.NEXT_PUBLIC_BEARER_FALLBACK;
const accountA = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }

afterEach(() => {
    cleanup();
    api.getMe = originalGetMe;
    globalThis.fetch = originalFetch;
    window.localStorage.clear();
    clearFreshLoginGrace();
    if (originalFallback === undefined) delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    else process.env.NEXT_PUBLIC_BEARER_FALLBACK = originalFallback;
});

function SignedInAs() {
    const { user } = useAuth();
    return <span>{user?.id ?? "checking"}</span>;
}

// A page's own data request starts in its effect, which React runs before the
// AuthProvider's effects, so it is sent before the sign-in check has answered.
function PageData({ onResult }: { onResult: (result: string) => void }) {
    useEffect(() => {
        const client = createHttpClient({ baseUrl: "http://example.test" });
        client.request<{ campaigns: string[] }>({ path: "/campaigns" }).then(
            body => onResult(body.campaigns.join(",")),
            error => onResult(error instanceof ApiClientError ? error.code ?? "api_error" : "error"),
        );
    }, [onResult]);
    return null;
}

test("a page refresh keeps data requested before the sign-in check confirms the same account", async () => {
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "false";
    const campaigns = deferred<Response>();
    globalThis.fetch = (() => campaigns.promise) as typeof fetch;
    api.getMe = async () => ({ ...accountA });
    let result: string | null = null;
    render(<AuthProvider><SignedInAs /><PageData onResult={value => { result = value; }} /></AuthProvider>);
    await screen.findByText("user-a");
    campaigns.resolve(Response.json({ campaigns: ["spring"] }));
    await waitFor(() => assert.notEqual(result, null));
    assert.equal(result, "spring");
});
