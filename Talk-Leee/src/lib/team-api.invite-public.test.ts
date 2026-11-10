import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { invalidateRequestIdentity, setTokenProvider } from "./http-client";
import { inviteApi } from "./team-api";

// QA F1-15 / D4-2: the invited person is signed out. AuthProvider still arms
// the client's identity fence on every page, and the fence refuses requests
// that need a verified account. The two invite calls are pre-auth, like
// signup, so the fence must let them through. The page tests mock inviteApi
// and could not see this.
const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
    setTokenProvider(null);
});

test("a signed-out visitor can preview and accept an invite", async () => {
    setTokenProvider(() => null);
    invalidateRequestIdentity();
    const sent: string[] = [];
    globalThis.fetch = (async (url: RequestInfo | URL) => {
        const path = new URL(String(url)).pathname;
        sent.push(path.slice(path.indexOf("/auth/")));
        const body = path.endsWith("/preview")
            ? { email: "sam@example.com", workspace_name: "Acme Ltd", role: "agent", expires_at: "2026-11-18T09:00:00Z" }
            : { email: "sam@example.com", message: "joined" };
        return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
    const token = "tok-abcdefghijklmnopqrstuvwxyz";
    assert.equal((await inviteApi.preview(token)).workspace_name, "Acme Ltd");
    const joined = await inviteApi.accept({ token, name: "Sam", password: "x".repeat(16), confirmPassword: "x".repeat(16) });
    assert.equal(joined.email, "sam@example.com");
    assert.deepEqual(sent, ["/auth/invites/preview", "/auth/invites/accept"]);
});
