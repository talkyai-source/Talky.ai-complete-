import { afterEach, mock, test } from "node:test";
import assert from "node:assert/strict";
import { ApiClientError, createHttpClient } from "./http-client";

// QA F1-27 (QA-UI-093): saving while offline showed "Save failed — Failed to
// fetch", the browser's own text. Network failures now say what happened in
// plain words, depending on whether the browser knows it is offline.
const originalFetch = globalThis.fetch;
let online = true;
afterEach(() => { globalThis.fetch = originalFetch; mock.restoreAll(); online = true; });

function failNetwork() {
    mock.getter(Object.getPrototypeOf(window.navigator), "onLine", () => online);
    globalThis.fetch = (async () => { throw new TypeError("Failed to fetch"); }) as typeof fetch;
    return createHttpClient({ baseUrl: "http://example.test", getToken: () => null });
}
function networkError(message: string) {
    return (error: unknown) => {
        assert.ok(error instanceof ApiClientError);
        assert.equal(error.code, "network_error");
        assert.equal(error.message, message);
        assert.ok(error.details instanceof TypeError, "the browser's error is kept for diagnosis");
        return true;
    };
}

for (const kind of ["request", "requestRaw"] as const) {
    test(`${kind}: an unreachable server is described, not 'Failed to fetch'`, async () => {
        const client = failNetwork();
        const save = kind === "request"
            ? client.request({ path: "/contacts", method: "POST", body: { phone: "+447400123456" } })
            : client.requestRaw({ path: "/contacts", method: "POST", body: { phone: "+447400123456" } });
        await assert.rejects(save, networkError("Could not reach the server. Check your connection, then try again."));
    });

    test(`${kind}: offline says offline`, async () => {
        online = false;
        const client = failNetwork();
        const save = kind === "request"
            ? client.request({ path: "/contacts", method: "POST", body: {} })
            : client.requestRaw({ path: "/contacts", method: "POST", body: {} });
        await assert.rejects(save, networkError("You appear to be offline. Reconnect, then try again."));
    });
}
