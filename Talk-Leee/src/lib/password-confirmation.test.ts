import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { api } from "./api";
import { ApiClientError, clearFreshLoginGrace, resetSessionExpiredLatch,
    setRequestIdentity, setSessionExpiredHandler, setTokenProvider } from "./http-client";

const originalFetch = globalThis.fetch;
afterEach(() => {
    globalThis.fetch = originalFetch;
    setSessionExpiredHandler(null);
    setTokenProvider(null);
    setRequestIdentity(null);
    clearFreshLoginGrace();
});

for (const operation of ["change-password", "disable-mfa"] as const) {
    for (const status of [400, 401]) {
        test(`${operation} confirmation rejection ${status} stays inline and retains the session`, async () => {
            let token: string | null = "synthetic-session";
            let expired = 0;
            const paths: string[] = [];
            setTokenProvider(() => token, value => { token = value; });
            setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
            setSessionExpiredHandler(() => { expired++; });
            resetSessionExpiredLatch();
            clearFreshLoginGrace();
            globalThis.fetch = (async url => {
                paths.push(new URL(String(url)).pathname);
                return Response.json({ error: { code: "current_password_invalid",
                    message: "Current password is incorrect.", request_id: "synthetic-request" } }, { status });
            }) as typeof fetch;
            await assert.rejects(() => operation === "change-password"
                ? api.changePassword("wrong-current", "orchid lantern cobalt 2026!")
                : api.disableMfa("wrong-current"), (error: unknown) => {
                assert.ok(error instanceof ApiClientError);
                assert.equal(error.message, "Current password is incorrect.");
                return true;
            });
            assert.equal(expired, 0);
            assert.equal(token, "synthetic-session");
            assert.equal(paths.length, 1, "confirmation rejection must not refresh the login");
        });
    }
}
