import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { backendEndpoints } from "@/lib/backend-endpoints";
import { createHttpClient } from "@/lib/http-client";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const sessionId = "11111111-1111-4111-8111-111111111111";

for (const [name, descriptor, expectedPath, expectedMethod] of [
    ["logout-all", backendEndpoints.authLogoutAll, "/auth/logout-all", "POST"],
    ["session list", backendEndpoints.authSessionsList, "/sessions/active", "GET"],
    ["session revoke", backendEndpoints.authSessionsRevoke, `/sessions/${sessionId}`, "DELETE"],
] as const) {
    test(`auth endpoint metadata sends the canonical ${name} request`, async () => {
        const calls: { path: string; method: string }[] = [];
        globalThis.fetch = async (input, init) => {
            calls.push({ path: String(input), method: init?.method || "GET" });
            return new Response(JSON.stringify({ success: true }), {
                status: 200, headers: { "content-type": "application/json" },
            });
        };
        const client = createHttpClient({ baseUrl: "https://fixture.example.invalid/api/v1",
            getToken: () => "synthetic-session-token" });
        await client.request({ path: descriptor.path.replace("{session_id}", sessionId),
            method: descriptor.method });
        assert.deepEqual(calls, [{ path: `https://fixture.example.invalid/api/v1${expectedPath}`, method: expectedMethod }]);
    });
}
