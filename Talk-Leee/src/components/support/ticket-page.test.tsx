import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent } from "@testing-library/react";
import { renderWithQueryClient } from "@/test-utils/render";

/*
 * The ticket page's failed-write path with the real page, a stubbed layout and
 * a stubbed network: a write whose response is lost re-reads the ticket before
 * offering Retry, the re-read state is what the page shows, and Retry sends
 * the same request_id and nothing else.
 */

const TICKET = "44444444-4444-4444-8444-444444444444";
const STAMP = "2026-10-30T10:00:00Z";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/link") return { __esModule: true, default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> };
    if (id === "next/navigation") return { useParams: () => ({ id: TICKET }), useRouter: () => ({ push() {}, replace() {}, prefetch() {} }), usePathname: () => `/support/tickets/${TICKET}` };
    if (id.includes("layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <div>{children}</div> };
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user: { id: "user-1", tenant_id: "tenant-1", email: "a@b.c", role: "user" }, status: "authenticated", loading: false }) };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    return originalLoad.call(this, id, ...args);
};
let SupportTicketPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    SupportTicketPage = require("../../app/support/tickets/[id]/page").default;
} finally {
    loader._load = originalLoad;
}

const config = {
    enabled: true, bot_enabled: true, permissions: { report: true, read_team: false },
    categories: [{ id: "other", label: "Something else" }], suggestions: [],
    attachments: { max_mb: 10, max_per_message: 5, types: ["png"] },
};
const message = (id: string, body: string) => ({ id, author_type: "customer", author_name: "Dev Operator", body, attachments: [], created_at: STAMP });
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

type Request = { method: string; path: string; body: Record<string, unknown> | null };
let requests: Request[] = [];
let serverTicket: Record<string, unknown>;
let writeHandler: (path: string, body: Record<string, unknown>) => Response;
const previousFetch = globalThis.fetch;

beforeEach(() => {
    requests = [];
    serverTicket = {
        id: TICKET, number: "SUP-000077", subject: "Caller ID was wrong", category: "other", priority: "high", status: "open",
        created_at: STAMP, updated_at: STAMP, requester: { id: "user-1", name: "Dev Operator" }, related: {},
        messages: [message("m1", "The caller ID is wrong.")], csat: null, allowed_actions: ["reply", "resolve"],
    };
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        const path = String(url);
        const method = init?.method ?? "GET";
        const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null;
        requests.push({ method, path, body });
        if (path.endsWith("/support/config")) return json(config);
        if (path.endsWith(`/support/tickets/${TICKET}`) && method === "GET") return json(serverTicket);
        if (method === "POST" && path.includes(`/support/tickets/${TICKET}/`)) return writeHandler(path, body ?? {});
        return json({ error: { code: "not_found", message: "no", details: null, request_id: "r" } }, 404);
    }) as typeof fetch;
});
afterEach(() => { cleanup(); globalThis.fetch = previousFetch; });

async function settle() {
    for (let round = 0; round < 8; round += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)); });
}

const reads = () => requests.filter((request) => request.method === "GET" && request.path.endsWith(`/support/tickets/${TICKET}`)).length;
const writes = () => requests.filter((request) => request.method === "POST");

test("a reply whose response is lost: the ticket is re-read and the saved reply shown; Retry reuses the request_id", async () => {
    let attempts = 0;
    writeHandler = (_path, body) => {
        attempts += 1;
        if (attempts === 1) {
            // The server saved the reply, but the response never arrived.
            serverTicket = { ...serverTicket, messages: [...(serverTicket.messages as unknown[]), message("m2", String(body.body))] };
            throw new TypeError("Failed to fetch");
        }
        return json(message("m2", String(body.body)), 200, { "Idempotent-Replay": "true" });
    };
    renderWithQueryClient(<SupportTicketPage />);
    await settle();
    assert.equal(document.querySelector("[data-support-ticket-number]")?.textContent, "SUP-000077");
    const textarea = document.querySelector<HTMLTextAreaElement>("textarea[id$='-reply']")!;
    assert.ok(textarea, "reply box rendered");
    fireEvent.change(textarea, { target: { value: "Thanks, confirmed." } });
    await act(async () => { fireEvent.submit(textarea.closest("form")!); });
    await settle();

    assert.match(document.querySelector("[data-support-thread]")?.textContent ?? "", /Thanks, confirmed\./, "the re-read ticket shows the reply the server saved");
    const alert = document.querySelector("[role='alert']");
    assert.ok(alert, "the unconfirmed write is reported");
    assert.match(alert.textContent ?? "", /could not be confirmed/);
    assert.equal(reads(), 2, "the first read plus one re-read after the lost response");
    assert.equal(writes().length, 1, "the reply was sent once so far");

    const retry = Array.from(alert.querySelectorAll("button")).find((button) => button.textContent === "Retry")!;
    await act(async () => { fireEvent.click(retry); });
    await settle();
    const sent = writes();
    assert.equal(sent.length, 2, "Retry sent exactly one more request");
    assert.equal(sent[0]!.body!.request_id, sent[1]!.body!.request_id, "the retry reused the request_id");
    assert.deepEqual(sent[1]!.body, sent[0]!.body, "the same request, field for field");
    assert.ok(sent.every((request) => request.path.endsWith(`/support/tickets/${TICKET}/messages`)), "never a different request");
    assert.equal(reads(), 3, "the successful retry re-read the ticket once more");
    assert.equal(document.querySelector("[role='alert']"), null, "the failure is cleared");
    assert.equal(document.querySelector<HTMLTextAreaElement>("textarea[id$='-reply']")?.value, "", "the reply box is emptied");
});

test("a resolve whose response is lost: the re-read ticket shows Resolved, and Retry carries the same request_id", async () => {
    let attempts = 0;
    writeHandler = (_path, body) => {
        attempts += 1;
        serverTicket = { ...serverTicket, status: "resolved", allowed_actions: ["reply", "reopen", "rate"] };
        if (attempts === 1) throw new TypeError("Failed to fetch");
        return json({ ...serverTicket, replayed_request: body.request_id }, 200, { "Idempotent-Replay": "true" });
    };
    renderWithQueryClient(<SupportTicketPage />);
    await settle();
    const resolve = Array.from(document.querySelectorAll("button")).find((button) => button.textContent === "Mark as resolved")!;
    assert.ok(resolve, "the open ticket offers Mark as resolved");
    await act(async () => { fireEvent.click(resolve); });
    await settle();

    assert.equal(reads(), 2, "one re-read after the lost response");
    assert.ok(Array.from(document.querySelectorAll("button")).some((button) => button.textContent === "Reopen"), "the re-read state (resolved) is what the page shows");
    assert.ok(!Array.from(document.querySelectorAll("button")).some((button) => button.textContent === "Mark as resolved"));
    assert.match(document.querySelector("[data-support-ticket]")?.textContent ?? "", /Resolved/);
    const alert = document.querySelector("[role='alert']");
    assert.ok(alert, "the unconfirmed write is reported");

    const retry = Array.from(alert.querySelectorAll("button")).find((button) => button.textContent === "Retry")!;
    await act(async () => { fireEvent.click(retry); });
    await settle();
    const sent = writes();
    assert.equal(sent.length, 2);
    assert.equal(sent[0]!.body!.request_id, sent[1]!.body!.request_id, "the retry reused the request_id");
    assert.ok(sent.every((request) => request.path.endsWith(`/support/tickets/${TICKET}/resolve`)), "never a different request");
    assert.equal(document.querySelector("[role='alert']"), null, "the failure is cleared");
});
