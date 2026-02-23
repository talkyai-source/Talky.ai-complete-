import { test } from "node:test";
import assert from "node:assert/strict";

test("backendApi.email.templates.list parses templates and maps fields", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(
            JSON.stringify({
                items: [
                    {
                        id: "tpl-1",
                        name: "Welcome",
                        html_content: "<p>Hello</p>",
                        thumbnail_url: null,
                        is_locked: true,
                        updated_at: "2026-01-01T00:00:00Z",
                    },
                ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
        );
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.email.templates.list();
        assert.equal(res.items.length, 1);
        assert.equal(res.items[0]!.id, "tpl-1");
        assert.equal(res.items[0]!.name, "Welcome");
        assert.equal(res.items[0]!.html, "<p>Hello</p>");
        assert.equal(res.items[0]!.thumbnailUrl, undefined);
        assert.equal(res.items[0]!.locked, true);
        assert.equal(res.items[0]!.updatedAt, "2026-01-01T00:00:00Z");
        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/email\/templates$/);
        assert.equal(calls[0]!.init?.method, "GET");
    } finally {
        globalThis.fetch = prevFetch;
    }
});

test("backendApi.email.send posts template_id and returns normalized messageId", async () => {
    const prevFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        return new Response(JSON.stringify({ message_id: "msg-1", status: "queued" }), {
            status: 200,
            headers: { "content-type": "application/json" },
        });
    }) as typeof fetch;

    try {
        process.env.NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000/api/v1";
        const { backendApi } = await import("@/lib/backend-api");
        const res = await backendApi.email.send({
            to: ["a@x.com", "b@x.com"],
            templateId: "tpl-1",
            subject: "Subject",
            html: "<p>Custom</p>",
        });
        assert.equal(res.messageId, "msg-1");
        assert.equal(res.status, "queued");

        assert.equal(calls.length, 1);
        assert.match(calls[0]!.url, /\/email\/send$/);
        assert.equal(calls[0]!.init?.method, "POST");

        const body = typeof calls[0]!.init?.body === "string" ? (JSON.parse(calls[0]!.init!.body as string) as unknown) : undefined;
        assert.ok(body && typeof body === "object");
        const b = body as Record<string, unknown>;
        assert.deepEqual(b.to, ["a@x.com", "b@x.com"]);
        assert.equal(b.template_id, "tpl-1");
        assert.equal(b.subject, "Subject");
        assert.equal(b.html, "<p>Custom</p>");
    } finally {
        globalThis.fetch = prevFetch;
    }
});
