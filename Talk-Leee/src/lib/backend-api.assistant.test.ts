import { test } from "node:test";
import assert from "node:assert/strict";

test("backendApi.assistantActions.list calls assistant actions list endpoint", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.assistantActions.list();
        assert.deepEqual(res, { items: [] });
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/assistant\/actions$/);
        assert.equal(calls[0]!.init?.method, "GET");
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.assistantRuns.list supports filtering and sorting query params", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }), { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        await backendApi.assistantRuns.list({
            page: 1,
            pageSize: 50,
            statuses: ["pending", "failed"],
            actionType: "notes:add",
            leadId: "lead-1",
            from: "2026-01-01T00:00:00.000Z",
            to: "2026-01-02T00:00:00.000Z",
            sortKey: "createdAt",
            sortDir: "desc",
        });

        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/assistant\/runs\?/);
        assert.match(calls[0]!.url, /status=pending%2Cfailed/);
        assert.match(calls[0]!.url, /action_type=notes%3Aadd/);
        assert.match(calls[0]!.url, /lead_id=lead-1/);
        assert.match(calls[0]!.url, /sort_key=createdAt/);
        assert.match(calls[0]!.url, /sort_dir=desc/);
        assert.equal(calls[0]!.init?.method, "GET");
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.assistant.plan posts payload to plan endpoint", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ planId: "plan-1", summary: "ok", steps: [{ step: 1 }], estimatedImpact: { writes: 1 } }), {
            status: 200,
            headers: { "content-type": "application/json" },
        });
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const plan = await backendApi.assistant.plan({ actionType: "notes:add", source: "dashboard", leadId: "lead-1", context: { note: "hi" } });
        assert.equal(plan.planId, "plan-1");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/assistant\/plan$/);
        assert.equal(calls[0]!.init?.method, "POST");
        const rawBody = calls[0]!.init?.body;
        assert.equal(typeof rawBody, "string");
        const body = JSON.parse(rawBody as string) as Record<string, unknown>;
        assert.equal(body.action_type, "notes:add");
        assert.equal(body.source, "dashboard");
        assert.equal(body.lead_id, "lead-1");
        assert.deepEqual(body.context, { note: "hi" });
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.assistant.execute posts payload to execute endpoint and parses run", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                id: "run-1",
                action_type: "notes:add",
                source: "dashboard",
                lead_id: "lead-1",
                status: "in_progress",
                created_at: "2026-01-14T10:00:00Z",
                started_at: "2026-01-14T10:00:01Z",
                completed_at: null,
                result: null,
                request_payload: { action_type: "notes:add", lead_id: "lead-1" },
            }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const run = await backendApi.assistant.execute({ actionType: "notes:add", source: "dashboard", leadId: "lead-1", context: { note: "hi" } });
        assert.equal(run.id, "run-1");
        assert.equal(run.actionType, "notes:add");
        assert.equal(run.status, "in_progress");
        assert.equal(run.leadId, "lead-1");

        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/assistant\/execute$/);
        assert.equal(calls[0]!.init?.method, "POST");
        const rawBody = calls[0]!.init?.body;
        assert.equal(typeof rawBody, "string");
        const body = JSON.parse(rawBody as string) as Record<string, unknown>;
        assert.equal(body.action_type, "notes:add");
        assert.equal(body.source, "dashboard");
        assert.equal(body.lead_id, "lead-1");
        assert.deepEqual(body.context, { note: "hi" });
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.assistantRuns.retry posts to retry endpoint", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                id: "run-2",
                action_type: "demo:fail",
                source: "dashboard",
                lead_id: "lead-1",
                status: "pending",
                created_at: "2026-01-14T10:00:00Z",
            }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const run = await backendApi.assistantRuns.retry("run-1");
        assert.equal(run.id, "run-2");
        assert.equal(run.actionType, "demo:fail");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/assistant\/runs\/run-1\/retry$/);
        assert.equal(calls[0]!.init?.method, "POST");
    } finally {
        globalThis.fetch = prevFetch;
    }
});
