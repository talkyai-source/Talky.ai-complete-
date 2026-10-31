import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiClientError } from "@/lib/http-client";
import {
    articleSearchSchema, attachmentUploadSchema, botReplySchema, conversationSchema, conversationStartSchema,
    describeSupportError, escalateResultSchema, escalationSchema, feedbackResultSchema, isSupportUnavailable,
    supportApi, supportConfigSchema, supportErrorCode, ticketListItemSchema, ticketMessageSchema, ticketPageSchema,
    ticketSchema, unreadSchema,
} from "@/lib/support-api";

const stamp = "2026-10-30T10:00:00+00:00";
const uuid = "11111111-1111-4111-8111-111111111111";

const config = {
    enabled: true, bot_enabled: true, permissions: { report: true, read_team: false },
    categories: [{ id: "billing", label: "Billing and payments" }], suggestions: ["My campaign isn't making calls"],
    attachments: { max_mb: 10, max_per_message: 5, types: ["png", "jpg", "webp", "pdf", "txt", "log", "csv"] },
};
const botMessage = { id: uuid, role: "assistant", content: "Hello", sources: [{ slug: "billing-intro", title: "Billing", url: "/docs/billing" }], created_at: stamp };
const escalation = {
    reason: "customer_requested", priority_hint: "normal",
    draft: { subject: "Help needed", category: "other", description: "Problem:\n- help" },
    existing_ticket: null,
};
const message = { id: uuid, author_type: "customer", author_name: "Ada", body: "Hi", attachments: [{ id: uuid, filename: "a.png", size_bytes: 10, content_type: "image/png" }], created_at: stamp };
const ticket = {
    id: uuid, number: "SUP-000123", subject: "Calls fail", category: "calls_campaigns", priority: "normal", status: "open",
    created_at: stamp, updated_at: stamp, requester: { id: uuid, name: "Ada" }, related: { campaign_id: uuid },
    messages: [message], csat: null, allowed_actions: ["reply", "resolve"],
};
const listItem = { id: uuid, number: "SUP-000123", subject: "Calls fail", status: "open", priority: "normal", category: "bug", updated_at: stamp, unread: true };

const shapes: Array<[string, { safeParse: (v: unknown) => { success: boolean } }, unknown, unknown]> = [
    ["config", supportConfigSchema, config, { ...config, permissions: { report: "yes" } }],
    ["config while off", supportConfigSchema, { ...config, enabled: false, categories: [], suggestions: [], attachments: { max_mb: 0, max_per_message: 0, types: [] } }, { enabled: false }],
    ["article search", articleSearchSchema, { items: [{ slug: "a", title: "A", section: "", snippet: "…", url: "/docs/a" }] }, { items: [{ slug: "a" }] }],
    ["conversation start", conversationStartSchema, { conversation_id: uuid, greeting: "Hi", suggestions: [] }, { conversation_id: uuid, greeting: "Hi" }],
    ["conversation", conversationSchema, { conversation_id: uuid, status: "active", messages: [botMessage], escalation: null }, { conversation_id: uuid, status: "paused", messages: [], escalation: null }],
    ["bot reply", botReplySchema, { message: botMessage, resolution_prompt: true, escalation: escalation }, { message: { ...botMessage, role: "system" }, resolution_prompt: false, escalation: null }],
    ["feedback", feedbackResultSchema, { status: "resolved_by_bot", message: null, escalation: null }, { status: "resolved_by_bot", message: "thanks", escalation: null }],
    ["escalate", escalateResultSchema, { escalation }, { escalation: { ...escalation, priority_hint: "critical" } }],
    ["escalation with an existing ticket", escalationSchema, { ...escalation, existing_ticket: { id: uuid, number: "SUP-000001", subject: "Old", status: "open" } }, { ...escalation, existing_ticket: { id: uuid, number: "1", subject: "Old", status: "open" } }],
    ["attachment upload", attachmentUploadSchema, { attachment_id: uuid, filename: "a.png", content_type: "image/png", size_bytes: 5 }, { attachment_id: uuid, filename: "a.png", content_type: "image/png", size_bytes: -1 }],
    ["ticket message", ticketMessageSchema, message, { ...message, attachments: "none" }],
    ["ticket", ticketSchema, ticket, { ...ticket, allowed_actions: ["delete"] }],
    ["ticket list item", ticketListItemSchema, listItem, { ...listItem, unread: "yes" }],
    ["ticket page", ticketPageSchema, { items: [listItem], next_cursor: null }, { items: [listItem] }],
    ["unread", unreadSchema, { count: 1, ticket_ids: [uuid] }, { count: "1", ticket_ids: [] }],
];

for (const [name, schema, valid, invalid] of shapes) {
    test(`${name}: the real shape parses and a malformed one is rejected`, () => {
        assert.equal(schema.safeParse(valid).success, true, `${name} valid`);
        assert.equal(schema.safeParse(invalid).success, false, `${name} invalid`);
    });
}

test("nullable fields: csat, next_cursor, author_name and escalation accept null", () => {
    const rated = ticketSchema.safeParse({ ...ticket, csat: { score: 5, comment: null }, status: "resolved", allowed_actions: [] });
    assert.equal(rated.success, true);
    assert.equal(ticketMessageSchema.safeParse({ ...message, author_name: null }).success, true);
    assert.equal(ticketPageSchema.safeParse({ items: [], next_cursor: "abc" }).success, true);
    assert.equal(ticketSchema.safeParse({ ...ticket, csat: { score: 6, comment: null } }).success, false);
});

test("a full page of 25 items parses and keeps its cursor", () => {
    const items = Array.from({ length: 25 }, (_, index) => ({ ...listItem, id: `${index}`, number: `SUP-${String(index + 1).padStart(6, "0")}` }));
    const page = ticketPageSchema.parse({ items, next_cursor: "MjAyNi0xMC0zMA" });
    assert.equal(page.items.length, 25);
    assert.equal(page.next_cursor, "MjAyNi0xMC0zMA");
});

test("unknown author types are kept for the thread to ignore, unknown statuses are rejected", () => {
    assert.equal(ticketMessageSchema.safeParse({ ...message, author_type: "robot" }).success, true);
    assert.equal(ticketSchema.safeParse({ ...ticket, status: "archived" }).success, false);
});

function error(status: number, code: string, extra: Partial<ConstructorParameters<typeof ApiClientError>[0]> = {}) {
    return new ApiClientError({ status, code, message: `server says ${code}`, url: "/x", method: "POST", ...extra });
}

test("each error envelope maps to one user-facing message", () => {
    assert.match(describeSupportError(error(429, "rate_limited", { retryAfterMs: 4000 })), /too quickly.*4s/);
    assert.match(describeSupportError(error(429, "rate_limited")), /too quickly/);
    assert.match(describeSupportError(error(409, "request_in_progress")), /still being processed/);
    assert.match(describeSupportError(error(409, "request_id_conflict")), /already used/);
    assert.match(describeSupportError(error(409, "conversation_closed")), /has ended/);
    assert.match(describeSupportError(error(409, "conversation_already_escalated")), /already opened/);
    assert.match(describeSupportError(error(409, "invalid_status_transition")), /not allowed/);
    assert.equal(describeSupportError(error(404, "not_found")), "Support isn't available yet.");
    assert.equal(describeSupportError(error(503, "support_unavailable")), "server says support_unavailable");
    assert.equal(describeSupportError(new Error("boom")), "boom");
    assert.equal(supportErrorCode(error(409, "version_conflict")), "version_conflict");
    assert.equal(supportErrorCode({ code: 3 }), null);
    assert.equal(isSupportUnavailable(error(404, "not_found")), true);
    assert.equal(isSupportUnavailable(error(403, "missing_permission")), false);
});

function stubFetch(handler: (url: string, init?: RequestInit) => Response) {
    const previous = globalThis.fetch;
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => handler(String(url), init)) as typeof fetch;
    return () => { globalThis.fetch = previous; };
}

test("a write reports a replay from the Idempotent-Replay header and the real status", async () => {
    const seen: Array<{ url: string; body: unknown }> = [];
    const restore = stubFetch((url, init) => {
        seen.push({ url, body: JSON.parse(String(init?.body)) });
        const replay = seen.length > 1;
        return new Response(JSON.stringify({ conversation_id: uuid, greeting: "Hi", suggestions: [] }), {
            status: replay ? 200 : 201,
            headers: { "content-type": "application/json", ...(replay ? { "Idempotent-Replay": "true" } : {}) },
        });
    });
    try {
        const first = await supportApi.startConversation("req-1", { route: "/dashboard" });
        const second = await supportApi.startConversation("req-1", { route: "/dashboard" });
        assert.equal(first.status, 201);
        assert.equal(first.replayed, false);
        assert.equal(second.status, 200);
        assert.equal(second.replayed, true);
        assert.deepEqual(seen.map((call) => (call.body as { request_id: string }).request_id), ["req-1", "req-1"]);
        assert.match(seen[0]!.url, /\/support\/conversations$/);
    } finally {
        restore();
    }
});

test("a rejected write surfaces the server's envelope code and status", async () => {
    const restore = stubFetch(() => new Response(JSON.stringify({ error: { code: "request_in_progress", message: "wait", details: null, request_id: "r" } }), {
        status: 409, headers: { "content-type": "application/json" },
    }));
    try {
        await assert.rejects(supportApi.sendMessage(uuid, "req-2", "hello"), (err: unknown) =>
            supportErrorCode(err) === "request_in_progress" && (err as ApiClientError).status === 409);
    } finally {
        restore();
    }
});

test("a malformed success body is rejected instead of rendered", async () => {
    const restore = stubFetch(() => new Response(JSON.stringify({ count: "many" }), { status: 200, headers: { "content-type": "application/json" } }));
    try {
        await assert.rejects(supportApi.unread(), /Unexpected unread count response/);
    } finally {
        restore();
    }
});

test("the upload sends one multipart field named file", async () => {
    const bodies: unknown[] = [];
    const restore = stubFetch((_url, init) => {
        bodies.push(init?.body);
        return new Response(JSON.stringify({ attachment_id: uuid, filename: "a.txt", content_type: "text/plain", size_bytes: 3 }), { status: 201, headers: { "content-type": "application/json" } });
    });
    try {
        const result = await supportApi.uploadAttachment(new File(["abc"], "a.txt", { type: "text/plain" }));
        assert.equal(result.attachment_id, uuid);
        const body = bodies[0];
        assert.ok(body instanceof FormData);
        assert.deepEqual([...body.keys()], ["file"]);
    } finally {
        restore();
    }
});
