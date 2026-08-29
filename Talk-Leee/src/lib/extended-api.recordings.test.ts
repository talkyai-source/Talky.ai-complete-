import assert from "node:assert/strict";
import { test } from "node:test";

import { extendedApi } from "@/lib/extended-api";
import { isApiClientError } from "@/lib/http-client";

test("recording playback and download use distinct authorized endpoints", async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(new Blob(["audio"], { type: "audio/wav" }), { status: 200 });
    }) as typeof fetch;

    try {
        const playback = await extendedApi.fetchRecordingPlaybackBlob("recording-1");
        const download = await extendedApi.downloadRecordingBlob("recording-1");

        assert.equal(playback.type, "audio/wav");
        assert.equal(download.type, "audio/wav");
        assert.match(calls[0]!.url, /\/recordings\/recording-1\/stream$/);
        assert.match(calls[1]!.url, /\/recordings\/recording-1\/download$/);
        assert.equal(calls[0]!.init?.method, "GET");
        assert.equal(calls[1]!.init?.method, "GET");
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("recording delete sends the reason and caller-owned idempotency key", async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(null, { status: 204 });
    }) as typeof fetch;

    try {
        await extendedApi.deleteRecording("recording-2", {
            reason: "Customer requested erasure",
            idempotencyKey: "recording-delete-request-1",
        });

        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/recordings\/recording-2$/);
        assert.equal(calls[0]!.init?.method, "DELETE");
        assert.equal(
            new Headers(calls[0]!.init?.headers).get("Idempotency-Key"),
            "recording-delete-request-1",
        );
        assert.deepEqual(JSON.parse(String(calls[0]!.init?.body)), {
            reason: "Customer requested erasure",
        });
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test("recording delete preserves the backend legal-hold error code", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async () => new Response(
        JSON.stringify({
            error: {
                code: "recording_legal_hold",
                message: "Recording is protected by legal hold",
                details: null,
            },
        }),
        { status: 423, headers: { "content-type": "application/json" } },
    )) as typeof fetch;

    try {
        await assert.rejects(
            extendedApi.deleteRecording("recording-3", {
                reason: "Customer requested erasure",
                idempotencyKey: "recording-delete-request-2",
            }),
            (error: unknown) => {
                assert.equal(isApiClientError(error), true);
                if (!isApiClientError(error)) return false;
                assert.equal(error.status, 423);
                assert.equal(error.code, "recording_legal_hold");
                return true;
            },
        );
    } finally {
        globalThis.fetch = originalFetch;
    }
});
