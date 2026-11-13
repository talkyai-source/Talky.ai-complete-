import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { renderWithQueryClient } from "@/test-utils/render";
import { supportChatStore } from "@/lib/support-chat-store";
import type { PageContext, SupportConfig } from "@/lib/support-api";

/*
 * The drawer's accessibility contract and the retry path, with the real
 * drawer and chat machine, a stubbed network and the motion primitive
 * replaced by a plain element.
 */

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/link") return { __esModule: true, default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> };
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, replace() {}, prefetch() {} }), usePathname: () => "/dashboard" };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id === "framer-motion") {
        const passthrough = (tag: string) => function Motion({ children, initial: _i, animate: _a, exit: _e, transition: _t, ...rest }: Record<string, unknown> & { children?: React.ReactNode }) {
            return React.createElement(tag, rest, children);
        };
        return { AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>, motion: { aside: passthrough("aside"), button: passthrough("button"), div: passthrough("div") } };
    }
    return originalLoad.call(this, id, ...args);
};
let SupportDrawer: typeof import("./support-drawer").SupportDrawer;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    SupportDrawer = require("./support-drawer").SupportDrawer;
} finally {
    loader._load = originalLoad;
}

const config: SupportConfig = {
    enabled: true, bot_enabled: true, permissions: { report: true, read_team: false },
    categories: [{ id: "other", label: "Something else" }], suggestions: ["Billing question"],
    attachments: { max_mb: 10, max_per_message: 5, types: ["png"] },
};
const identity = { tenantId: "tenant-1", userId: "user-1" };
const uuid = "11111111-1111-4111-8111-111111111111";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

let requests: Array<{ path: string; body: unknown }> = [];
/** POST /support/conversations: the start of a conversation. */
let startHandler: (body: { request_id: string; page_context: PageContext }) => Response | Promise<Response>;
/** POST /support/tickets: the ticket form's submission. */
let ticketHandler: ((body: Record<string, unknown>) => Response | Promise<Response>) | null;
let messageHandler: (body: { request_id: string; content: string }) => Response | Promise<Response>;
/** GET /support/conversations/{id}: what the server holds when a lost write is reconciled. */
let conversationHandler: () => Response;
const previousFetch = globalThis.fetch;

beforeEach(() => {
    requests = [];
    window.sessionStorage.clear();
    supportChatStore.resetForTests();
    startHandler = () => json({ conversation_id: uuid, greeting: "Hi there", suggestions: ["Billing question"] }, 201);
    ticketHandler = null;
    messageHandler = () => json({ message: { id: uuid, role: "assistant", content: "Try **Check again**.", sources: [{ slug: "s", title: "Readiness", url: "/docs/outbound-campaigns#readiness" }], created_at: "2026-10-30T10:00:00Z" }, resolution_prompt: true, escalation: null });
    conversationHandler = () => json({ error: { code: "not_found", message: "no", details: null, request_id: "r" } }, 404);
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
        const path = String(url);
        const body = init?.body ? JSON.parse(String(init.body)) : null;
        requests.push({ path, body });
        if (path.endsWith("/support/conversations")) return startHandler(body);
        if (path.endsWith(`/support/conversations/${uuid}`) && (init?.method ?? "GET") === "GET") return conversationHandler();
        if (path.endsWith("/messages")) return messageHandler(body);
        if (path.endsWith("/support/tickets") && ticketHandler) return ticketHandler(body);
        return json({ error: { code: "not_found", message: "no", details: null, request_id: "r" } }, 404);
    }) as typeof fetch;
});
afterEach(() => { cleanup(); globalThis.fetch = previousFetch; });

async function settle() {
    for (let round = 0; round < 6; round += 1) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)); });
}

const drawer = (pageContext: () => PageContext) => <SupportDrawer config={config} identity={identity} pageContext={pageContext} />;

function mountOpen(pageContext: () => PageContext = () => ({ route: "/dashboard" })) {
    const opener = document.createElement("button");
    opener.textContent = "Help";
    document.body.appendChild(opener);
    opener.focus();
    const view = renderWithQueryClient(drawer(pageContext));
    act(() => supportChatStore.setOpen(true));
    return { ...view, opener };
}

test("the drawer is a modal dialog, announces bot replies politely, and Escape closes it and returns focus to the opener", async () => {
    const { opener } = mountOpen();
    await settle();
    const dialog = document.querySelector<HTMLElement>("[role='dialog']");
    assert.ok(dialog, "dialog rendered");
    assert.equal(dialog.getAttribute("aria-modal"), "true");
    assert.equal(dialog.getAttribute("aria-label"), "Support");
    assert.notEqual(document.activeElement, opener, "focus moved into the drawer");
    assert.ok(dialog.contains(document.activeElement), "focus is inside the dialog");
    const log = dialog.querySelector("[role='log']");
    assert.equal(log?.getAttribute("aria-live"), "polite");
    assert.match(log?.textContent ?? "", /Hi there/, "greeting shown");
    assert.match(document.querySelector("[data-support-includes]")?.textContent ?? "", /Includes: this page/);
    assert.equal(window.sessionStorage.getItem(`talklee.support.conversation.v1:tenant-1:user-1`), uuid, "conversation id stored for a reload");
    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });
    await settle();
    assert.equal(document.querySelector("[role='dialog']"), null, "closed");
    assert.equal(document.activeElement, opener, "focus returned to the opener");
});

test("sending a message shows the typing indicator, then the reply with a docs source chip and the resolution prompt", async () => {
    // Hold the reply until the indicator has been observed.
    let release: (() => void) | null = null;
    const original = messageHandler;
    messageHandler = async (body) => { await new Promise<void>((resolve) => { release = resolve; }); return original(body); };
    mountOpen();
    await settle();
    const input = document.querySelector<HTMLTextAreaElement>("#support-chat-input")!;
    fireEvent.change(input, { target: { value: "My campaign is stuck" } });
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    await settle();
    const statuses = Array.from(document.querySelectorAll("[role='status']")).map((node) => node.textContent ?? "");
    assert.ok(statuses.some((text) => text.includes("The assistant is typing")), `typing indicator announced, saw: ${JSON.stringify(statuses)}`);
    assert.ok(release, "the reply is still pending while the indicator shows");
    await act(async () => { release!(); });
    await settle();
    const log = document.querySelector("[role='log']")!;
    assert.match(log.textContent ?? "", /Check again/);
    assert.equal(log.querySelector("a[href='/docs/outbound-campaigns#readiness']")?.textContent, "Readiness");
    assert.match(document.body.textContent ?? "", /Did this solve your problem\?/);
    const sent = requests.find((request) => request.path.endsWith("/messages"))!;
    assert.equal((sent.body as { content: string }).content, "My campaign is stuck");
});

test("a failed send keeps its request_id: Retry sends the same body again, and the replayed answer is shown", async () => {
    let attempts = 0;
    messageHandler = (body) => {
        attempts += 1;
        if (attempts === 1) return json({ error: { code: "support_unavailable", message: "Support is temporarily unavailable.", details: null, request_id: "r" } }, 503);
        return json({ message: { id: uuid, role: "assistant", content: `Replayed for ${body.request_id}`, sources: [], created_at: "2026-10-30T10:00:00Z" }, resolution_prompt: false, escalation: null });
    };
    mountOpen();
    await settle();
    const input = document.querySelector<HTMLTextAreaElement>("#support-chat-input")!;
    fireEvent.change(input, { target: { value: "hello" } });
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    await settle();
    const alert = document.querySelector("[role='alert']");
    assert.ok(alert, "failure shown");
    assert.match(alert.textContent ?? "", /temporarily unavailable/);
    assert.ok(Array.from(alert.querySelectorAll("button")).some((button) => button.textContent === "Create a ticket instead"));
    const retry = Array.from(alert.querySelectorAll("button")).find((button) => button.textContent === "Retry")!;
    await act(async () => { fireEvent.click(retry); });
    await settle();
    const sends = requests.filter((request) => request.path.endsWith("/messages")).map((request) => request.body as { request_id: string; content: string });
    assert.equal(sends.length, 2);
    assert.equal(sends[0]!.request_id, sends[1]!.request_id, "the retry reused the request_id");
    assert.equal(sends[1]!.content, "hello");
    assert.match(document.querySelector("[role='log']")?.textContent ?? "", new RegExp(`Replayed for ${sends[0]!.request_id}`));
    assert.equal(document.querySelector("[role='alert']"), null);
});

test("Create a ticket instead drafts from the customer's words without sending another request", async () => {
    messageHandler = () => json({ error: { code: "support_unavailable", message: "down", details: null, request_id: "r" } }, 503);
    mountOpen();
    await settle();
    const input = document.querySelector<HTMLTextAreaElement>("#support-chat-input")!;
    fireEvent.change(input, { target: { value: "My caller ID shows the wrong number" } });
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    await settle();
    const before = requests.length;
    const instead = Array.from(document.querySelectorAll("button")).find((button) => button.textContent === "Create a ticket instead")!;
    await act(async () => { fireEvent.click(instead); });
    await settle();
    assert.equal(requests.length, before, "no new request was sent");
    const form = document.querySelector<HTMLFormElement>("[data-support-ticket-form]");
    assert.ok(form, "ticket form shown");
    assert.equal(form.querySelector<HTMLInputElement>("input")?.value, "My caller ID shows the wrong number");
});

const conversationWith = (messages: Array<{ role: "user" | "assistant"; content: string }>) => json({
    conversation_id: uuid, status: "active", escalation: null,
    messages: messages.map((message, index) => ({ id: `${uuid.slice(0, 8)}-${index}`, role: message.role, content: message.content, sources: [], created_at: "2026-10-30T10:00:00Z" })),
});
const conversationReads = () => requests.filter((request) => request.path.endsWith(`/support/conversations/${uuid}`) && request.body === null).length;
const messageSends = () => requests.filter((request) => request.path.endsWith("/messages")).map((request) => request.body as { request_id: string; content: string });

async function sendHello() {
    const input = document.querySelector<HTMLTextAreaElement>("#support-chat-input")!;
    fireEvent.change(input, { target: { value: "hello" } });
    await act(async () => { fireEvent.submit(input.closest("form")!); });
    await settle();
}

test("a message whose response is lost is re-read: the reply the server saved is shown and the message is not sent twice", async () => {
    messageHandler = () => { throw new TypeError("Failed to fetch"); };
    conversationHandler = () => conversationWith([{ role: "assistant", content: "Hi there" }, { role: "user", content: "hello" }, { role: "assistant", content: "Saved on the server" }]);
    mountOpen();
    await settle();
    await sendHello();
    assert.match(document.querySelector("[role='log']")?.textContent ?? "", /Saved on the server/, "the saved reply is shown");
    assert.equal(document.querySelector("[role='alert']"), null, "no failure and no Retry are shown");
    assert.equal(messageSends().length, 1, "the message was sent once");
    assert.equal(conversationReads(), 1, "the conversation was re-read once");
});

test("when the re-read holds no reply, Retry sends the same request_id and never a different request", async () => {
    let attempts = 0;
    messageHandler = (body) => {
        attempts += 1;
        if (attempts === 1) throw new TypeError("Failed to fetch");
        return json({ message: { id: uuid, role: "assistant", content: `Replayed for ${body.request_id}`, sources: [], created_at: "2026-10-30T10:00:00Z" }, resolution_prompt: false, escalation: null });
    };
    // The server never saw the message: the conversation still holds only the greeting.
    conversationHandler = () => conversationWith([{ role: "assistant", content: "Hi there" }]);
    mountOpen();
    await settle();
    await sendHello();
    const alert = document.querySelector("[role='alert']");
    assert.ok(alert, "the failure is shown after the re-read found nothing");
    // QA F1-27: the failure is described in plain words, not the browser's "Failed to fetch".
    assert.match(alert.textContent ?? "", /Could not reach the server/);
    assert.equal(conversationReads(), 1, "re-read once before offering Retry");
    assert.equal(messageSends().length, 1);
    const retry = Array.from(alert.querySelectorAll("button")).find((button) => button.textContent === "Retry")!;
    await act(async () => { fireEvent.click(retry); });
    await settle();
    const sends = messageSends();
    assert.equal(sends.length, 2, "Retry sent exactly one more message");
    assert.equal(sends[0]!.request_id, sends[1]!.request_id, "with the same request_id");
    assert.equal(sends[1]!.content, "hello", "and the same content");
    const posts = requests.filter((request) => request.body !== null).map((request) => request.path.replace(/^.*\/support/, ""));
    assert.deepEqual(posts, ["/conversations", `/conversations/${uuid}/messages`, `/conversations/${uuid}/messages`], "no other write went out");
    assert.match(document.querySelector("[role='log']")?.textContent ?? "", new RegExp(`Replayed for ${sends[0]!.request_id}`));
    assert.equal(document.querySelector("[role='alert']"), null);
});

const campaignId = "22222222-2222-4222-8222-222222222222";
const callId = "33333333-3333-4333-8333-333333333333";
const startBodies = () => requests
    .filter((request) => request.path.endsWith("/support/conversations") && request.body !== null)
    .map((request) => request.body as { request_id: string; page_context: PageContext });

test("QA F2-09: a failed start is retried with the page context it was started with, so another page cannot turn the retry into a request_id conflict", async () => {
    // The backend's rule (F2-09): the same request_id with another route, campaign or call is a 409.
    // The first response is lost; the server already holds the conversation, so only an identical retry is accepted.
    let firstContent: string | null = null;
    startHandler = (body) => {
        const content = JSON.stringify([body.page_context.route, body.page_context.campaign_id, body.page_context.call_id]);
        if (firstContent === null) {
            firstContent = content;
            return json({ error: { code: "support_unavailable", message: "Support is temporarily unavailable.", details: null, request_id: "r" } }, 503);
        }
        if (content !== firstContent) return json({ error: { code: "request_id_conflict", message: "This request_id was already used with different content. Generate a new one.", details: null, request_id: "r" } }, 409);
        return json({ conversation_id: uuid, greeting: "Hi there", suggestions: [] }, 201);
    };
    const onCampaign: PageContext = { route: `/campaigns/${campaignId}`, campaign_id: campaignId };
    const onCall: PageContext = { route: `/calls/${callId}`, call_id: callId };
    const view = mountOpen(() => onCampaign);
    await settle();
    assert.match(document.querySelector("[role='alert']")?.textContent ?? "", /temporarily unavailable/, "the first start failed");
    // The user opens another page: the launcher closes the drawer and hands it that page's context. Later the drawer is opened again.
    act(() => supportChatStore.setOpen(false));
    view.rerender(<QueryClientProvider client={view.qc}>{drawer(() => onCall)}</QueryClientProvider>);
    act(() => supportChatStore.setOpen(true));
    await settle();
    const retry = Array.from(document.querySelectorAll("[role='alert'] button")).find((button) => button.textContent === "Retry")!;
    assert.ok(retry, "Retry is offered for the failed start");
    await act(async () => { fireEvent.click(retry); });
    await settle();
    const starts = startBodies();
    assert.equal(starts.length, 2, "the retry sent exactly one more start");
    assert.equal(starts[1]!.request_id, starts[0]!.request_id, "with the same request_id");
    assert.deepEqual(starts[1]!.page_context, onCampaign, "and the page context the start was made with, not the page the user is on now");
    assert.equal(document.querySelector("[role='alert']"), null, "the server accepted the retry as the same request");
    assert.match(document.querySelector("[role='log']")?.textContent ?? "", /Hi there/);
});

test("a failed ticket submission is retried with the same body: the form is locked while the write is unconfirmed", async () => {
    // create_ticket reads the draft from the store when it executes, so the draft must not be editable between attempts.
    let attempts = 0;
    ticketHandler = () => {
        attempts += 1;
        if (attempts === 1) return json({ error: { code: "support_unavailable", message: "Support is temporarily unavailable.", details: null, request_id: "r" } }, 503);
        return json({
            id: uuid, number: "SUP-000123", subject: "hello", category: "other", priority: "normal", status: "open",
            created_at: "2026-10-30T10:00:00Z", updated_at: "2026-10-30T10:00:00Z",
            requester: { id: null, name: null }, related: {}, messages: [], csat: null, allowed_actions: [],
        }, 201);
    };
    messageHandler = () => json({ error: { code: "support_unavailable", message: "down", details: null, request_id: "r" } }, 503);
    mountOpen();
    await settle();
    await sendHello();
    const instead = Array.from(document.querySelectorAll("button")).find((button) => button.textContent === "Create a ticket instead")!;
    await act(async () => { fireEvent.click(instead); });
    await settle();
    const submit = Array.from(document.querySelectorAll("button")).find((button) => button.textContent === "Send to support")!;
    await act(async () => { fireEvent.click(submit); });
    await settle();
    const form = document.querySelector<HTMLFormElement>("[data-support-ticket-form]")!;
    assert.match(document.querySelector("[role='alert']")?.textContent ?? "", /temporarily unavailable/, "the first submission failed");
    assert.equal(form.querySelector<HTMLInputElement>("input")?.disabled, true, "the subject is locked");
    assert.equal(form.querySelector<HTMLTextAreaElement>("textarea")?.disabled, true, "the description is locked");
    const retry = Array.from(document.querySelectorAll("[role='alert'] button")).find((button) => button.textContent === "Retry")!;
    await act(async () => { fireEvent.click(retry); });
    await settle();
    const posts = requests.filter((request) => request.path.endsWith("/support/tickets")).map((request) => request.body as { request_id: string });
    assert.equal(posts.length, 2);
    assert.deepEqual(posts[1], posts[0], "the retry sent the same request_id and the same content");
    assert.match(document.body.textContent ?? "", /Your ticket was created/);
});
