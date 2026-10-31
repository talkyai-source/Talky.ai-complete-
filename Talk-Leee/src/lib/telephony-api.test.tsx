import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook } from "@testing-library/react";
import { ApiClientError } from "@/lib/http-client";
import { surfaceProblemDetail, useDeleteSipTrunk, useTestSipTrunk } from "./telephony-api";

const realFetch = globalThis.fetch;
const clients: QueryClient[] = [];
afterEach(() => {
    try {
        cleanup();
    } finally {
        // Unmount observers first; removing them can schedule mutation GC.
        clients.splice(0).forEach((client) => {
            client.getMutationCache().getAll().forEach((mutation) => mutation.destroy());
            client.clear();
        });
        globalThis.fetch = realFetch;
    }
});

function makeWrapper() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    return function Wrapper({ children }: { children: React.ReactNode }) {
        return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    };
}

/** What the shared client produces today for an `application/problem+json` response. */
function problemError(status: number, body: Record<string, unknown>, code = "http_error") {
    return new ApiClientError({ status, code, message: "Request failed", details: JSON.stringify(body), url: "http://x/telephony/sip/trunks/t", method: "DELETE" });
}

test("the server's detail replaces the generic 'Request failed' for a problem response", () => {
    const original = problemError(409, { type: "t", title: "Trunk In Use", status: 409, detail: "Finish active calls first.", instance: "/p" });
    const surfaced = surfaceProblemDetail(original);
    assert.ok(surfaced instanceof ApiClientError);
    const error = surfaced as ApiClientError;
    assert.equal(error.message, "Finish active calls first.");
    // Everything else about the failure is kept.
    assert.equal(error.status, 409);
    assert.equal(error.code, "http_error");
    assert.equal(error.url, original.url);
    assert.equal(error.method, "DELETE");
});

test("the title is used when a problem response has no detail", () => {
    assert.equal((surfaceProblemDetail(problemError(404, { title: "Trunk Not Found" })) as Error).message, "Trunk Not Found");
});

test("a problem carried as an object (already parsed) is read the same way", () => {
    const error = new ApiClientError({ status: 429, code: "rate_limited", message: "Rate limited", details: { title: "Too Many Requests", detail: "Wait 10 seconds before testing again." }, url: "u", method: "POST" });
    const surfaced = surfaceProblemDetail(error) as ApiClientError;
    assert.equal(surfaced.message, "Wait 10 seconds before testing again.");
    assert.equal(surfaced.code, "rate_limited");
});

test("field problems from a 422 are reported by what the person typed, not by API field names", () => {
    const error = new ApiClientError({
        status: 422, code: "validation_error", message: "Request payload failed validation", url: "u", method: "POST",
        details: [
            { type: "value_error", loc: ["body", "sip_domain"], msg: "Value error, sip_domain must be public; private targets require an explicit platform override" },
            { type: "string_too_short", loc: ["body", "trunk_name"], msg: "String should have at least 3 characters" },
        ],
    });
    const message = (surfaceProblemDetail(error) as Error).message;
    assert.match(message, /^Provider address must be public; private targets require an explicit platform override/);
    assert.match(message, /Line name: String should have at least 3 characters/);
    assert.doesNotMatch(message, /sip_domain|trunk_name|Value error/);
});

test("anything that is not a problem response comes back untouched", () => {
    const plain = new Error("boom");
    assert.equal(surfaceProblemDetail(plain), plain);
    assert.equal(surfaceProblemDetail("text"), "text");
    const already = new ApiClientError({ status: 500, code: "server_error", message: "An unexpected error occurred.", details: { request_id: "r" }, url: "u", method: "GET" });
    assert.equal(surfaceProblemDetail(already), already);
    const notJson = new ApiClientError({ status: 502, code: "server_error", message: "Server error", details: "<html>Bad gateway</html>", url: "u", method: "GET" });
    assert.equal(surfaceProblemDetail(notJson), notJson);
});

test("a refused delete reaches the page with the server's explanation, through the real client", async () => {
    // Reproduces the owner's "can't delete any PBX": the backend answers
    // application/problem+json, which the shared client used to report as "Request failed".
    const seen: Array<{ url: string; method: string }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
        seen.push({ url: String(url), method: String(init?.method) });
        return new Response(
            JSON.stringify({ type: "t", title: "Trunk In Use", status: 409, detail: "Finish active calls first.", instance: "/p" }),
            { status: 409, headers: { "content-type": "application/problem+json" } },
        );
    }) as typeof fetch;
    const { result } = renderHook(() => useDeleteSipTrunk(), { wrapper: makeWrapper() });
    await assert.rejects(
        () => result.current.mutateAsync("trunk-1"),
        (error: unknown) => {
            assert.ok(error instanceof ApiClientError);
            assert.equal((error as ApiClientError).message, "Finish active calls first.");
            assert.equal((error as ApiClientError).status, 409);
            return true;
        },
    );
    assert.deepEqual(seen.map((call) => call.method), ["DELETE"]);
    assert.match(seen[0].url, /\/telephony\/sip\/trunks\/trunk-1$/);
});

test("a too-frequent test reports the server's wait message, not 'Rate limited'", async () => {
    globalThis.fetch = (async () => new Response(
        JSON.stringify({ title: "Too Many Requests", status: 429, detail: "Wait 10 seconds before testing this line again." }),
        { status: 429, headers: { "content-type": "application/problem+json" } },
    )) as typeof fetch;
    const { result } = renderHook(() => useTestSipTrunk(), { wrapper: makeWrapper() });
    await assert.rejects(
        () => result.current.mutateAsync("trunk-1"),
        (error: unknown) => {
            assert.equal((error as ApiClientError).message, "Wait 10 seconds before testing this line again.");
            assert.equal((error as ApiClientError).code, "rate_limited");
            return true;
        },
    );
});
