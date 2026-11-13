import React from "react";
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { sharedHttpClient } from "@/lib/api";
import type { HttpRequestOptions } from "@/lib/http-client";
import { renderWithQueryClient } from "@/test-utils/render";
// Load the real data hooks (and through them the real API helper) BEFORE the
// shell stubs below, so only the page itself sees a stubbed shell.
import "@/lib/api-hooks";

// QA F6-03: the call history must page with the server's cursor mode, not
// ever-larger offsets. The real page, the real `useCalls` hook and the real
// `dashboardApi` run here; only the HTTP boundary and unrelated shell widgets
// are replaced (same approach as workflow-persistence.test.tsx).
const tenantA = { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", tenant_id: "11111111-1111-4111-8111-111111111111" };
const tenantB = { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", tenant_id: "22222222-2222-4222-8222-222222222222" };
let user = tenantA;
const permissions = { isSuccess: true, data: { permissions: ["calls:read"] } };
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main> };
    }
    if (id.endsWith("components/calls/dial-again-button")) return { DialAgainButton: () => null };
    if (id.endsWith("components/calls/call-issues-banner")) return { CallIssuesBanner: () => null };
    if (id.endsWith("lib/auth-context")) return { useAuth: () => ({ user, isLoading: false }) };
    if (id.endsWith("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissions };
    if (id.endsWith("lib/use-call-history-workflow")) {
        return { useCallHistoryWorkflow: () => ({ workflow: {}, update() {}, retry() {}, busy: false, failed: false, backupUnavailable: false }) };
    }
    return originalLoad.call(this, id, ...args);
};
let CallsPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- actual page with scoped shell stubs above
    CallsPage = require("./page").default;
} finally {
    loader._load = originalLoad;
}

if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

type Params = Record<string, unknown>;
let listRequests: Params[] = [];
let respond: (params: Params) => unknown;

const phone = (n: number) => `+1555000${1000 + n}`;

// The number a row headlines is the dialed number for outbound and the caller
// for inbound (page.tsx CallParties), so row n carries phone(n) in that slot.
function item(n: number, direction: "inbound" | "outbound" = "outbound") {
    return {
        id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
        timestamp: `2026-11-06T10:${String(n).padStart(2, "0")}:00Z`,
        from_number: direction === "inbound" ? phone(n) : "+15550000000",
        to_number: direction === "inbound" ? "+15550009999" : phone(n),
        status: "completed",
        direction,
        campaign_id: "campaign-1",
        campaign_name: "Cursor campaign",
    };
}

// Server stand-in: the first outbound page links to a second via its cursor;
// the inbound filter has its own chain. A cursor is only valid for the filters
// it was issued under (the real server answers 422 otherwise).
function server(params: Params) {
    if (params.direction === "inbound") {
        return params.cursor === "cursor-inbound-2"
            ? { items: [item(6, "inbound")], total: null, next_cursor: null }
            : { items: [item(5, "inbound")], total: null, next_cursor: "cursor-inbound-2" };
    }
    return params.cursor === "cursor-page-2"
        ? { items: [item(3)], total: null, next_cursor: null }
        : { items: [item(1), item(2)], total: null, next_cursor: "cursor-page-2" };
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => { resolve = done; });
    return { promise, resolve };
}

beforeEach(() => {
    user = tenantA;
    listRequests = [];
    respond = server;
    window.history.replaceState({}, "", "/calls");
    // jsdom does not implement these; the page's Select measures text and its rows pause audio on unmount.
    mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null);
    mock.method(window.HTMLMediaElement.prototype, "pause", () => {});
    mock.method(sharedHttpClient(), "request", async (options: HttpRequestOptions) => {
        assert.equal(options.path, "/calls", "Only the call-history list may be requested");
        const params: Params = { ...(options.params ?? {}) };
        listRequests.push(params);
        return respond(params);
    });
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
});

const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

test("the first page is requested in cursor mode, never with a page number", async () => {
    renderWithQueryClient(<CallsPage />);
    await screen.findAllByText(phone(1));

    const first = listRequests[0];
    assert.equal(first.pagination, "cursor", `first request must use cursor mode; sent ${JSON.stringify(first)}`);
    assert.equal(first.cursor, undefined, "the first page has no cursor yet");
    assert.equal(first.page, undefined, "cursor mode must not carry a page number");
    assert.equal(first.page_size, "50");
});

test("Next loads the following page with the cursor the server returned, and Previous returns", async () => {
    renderWithQueryClient(<CallsPage />);
    await screen.findAllByText(phone(1));
    assert.equal(button("Previous").disabled, true);
    assert.equal(button("Next").disabled, false);
    // The server sends no total in cursor mode; the page must not invent one.
    assert.equal(screen.queryByText(/ of \d+ calls/), null);

    fireEvent.click(button("Next"));
    await screen.findAllByText(phone(3));

    const continued = listRequests.find((params) => params.cursor !== undefined);
    assert.ok(continued, "loading the next page must send a cursor");
    assert.equal(continued.cursor, "cursor-page-2");
    assert.equal(continued.pagination, "cursor");
    assert.equal(continued.page, undefined);
    // The server said there is nothing after this page.
    await waitFor(() => assert.equal(button("Next").disabled, true));
    assert.equal(button("Previous").disabled, false);

    fireEvent.click(button("Previous"));
    await screen.findAllByText(phone(1));
    assert.equal(button("Previous").disabled, true);
    assert.ok(listRequests.every((params) => params.page === undefined), "no request may fall back to offset paging");
});

test("the filters are sent on the first request and replayed on every continuation, and a changed filter starts a new cursor chain", async () => {
    renderWithQueryClient(<CallsPage />);
    await screen.findAllByText(phone(1));
    fireEvent.click(button("Next"));
    await screen.findAllByText(phone(3));

    // Direction is a server-side filter: switching it must drop the outbound
    // cursor (the server rejects a cursor under different filters) and restart.
    fireEvent.click(button("inbound"));
    await screen.findAllByText(phone(5));
    const restarted = listRequests.at(-1)!;
    assert.equal(restarted.direction, "inbound");
    assert.equal(restarted.pagination, "cursor");
    assert.equal(restarted.cursor, undefined, "a new filter must start from the first page");

    fireEvent.click(button("Next"));
    await screen.findAllByText(phone(6));
    const continued = listRequests.at(-1)!;
    assert.equal(continued.cursor, "cursor-inbound-2");
    assert.equal(continued.direction, "inbound", "the continuation must replay the filter the cursor was issued under");
    assert.equal(continued.pagination, "cursor");

    for (const params of listRequests) {
        if (params.cursor === "cursor-page-2") assert.equal(params.direction, undefined);
        if (params.cursor === "cursor-inbound-2") assert.equal(params.direction, "inbound");
    }
});

test("search keeps narrowing the loaded page, including by the contact the caller gave, and returns to the first page", async () => {
    respond = (params) => params.cursor === "cursor-page-2"
        ? { items: [item(3)], total: null, next_cursor: null }
        : { items: [{ ...item(1), captured_email: "guide@example.test" }, item(2)], total: null, next_cursor: "cursor-page-2" };
    renderWithQueryClient(<CallsPage />);
    await screen.findAllByText(phone(1));
    fireEvent.click(button("Next"));
    await screen.findAllByText(phone(3));

    fireEvent.change(screen.getByRole("textbox", { name: /Search calls/ }), { target: { value: "guide@example" } });

    // Typing leaves the later page (as it always has) and filters the first one.
    await screen.findAllByText(phone(1));
    assert.equal(screen.queryAllByText(phone(2)).length, 0, "a row that does not match the term is hidden");
    assert.equal(screen.queryAllByText(phone(3)).length, 0);
    assert.equal(button("Previous").disabled, true);
});

test("Next stays off while the following page loads, so a double click cannot skip a page", async () => {
    const gate = deferred<unknown>();
    respond = (params) => (params.cursor === "cursor-page-2" ? gate.promise : server(params));
    renderWithQueryClient(<CallsPage />);
    await screen.findAllByText(phone(1));

    fireEvent.click(button("Next"));
    await waitFor(() => assert.ok(listRequests.some((params) => params.cursor === "cursor-page-2")));
    // The first page's rows stay up as placeholders, and the cursor they hold is
    // the one just used: it must not be offered again.
    assert.equal(button("Next").disabled, true);
    fireEvent.click(button("Next"));

    await act(async () => gate.resolve({ items: [item(3)], total: null, next_cursor: null }));
    await screen.findAllByText(phone(3));
    assert.equal(listRequests.filter((params) => params.cursor === "cursor-page-2").length, 1);
    // The stack advanced exactly once: one Previous is back on the first page.
    fireEvent.click(button("Previous"));
    await screen.findAllByText(phone(1));
    assert.equal(button("Previous").disabled, true);
});

test("a different signed-in tenant never reuses the previous tenant's cursor", async () => {
    const { qc, rerender } = renderWithQueryClient(<CallsPage />);
    await screen.findAllByText(phone(1));
    fireEvent.click(button("Next"));
    await screen.findAllByText(phone(3));
    assert.equal(listRequests.at(-1)!.cursor, "cursor-page-2");

    user = tenantB;
    rerender(<QueryClientProvider client={qc}><CallsPage /></QueryClientProvider>);

    // The cursor was issued to tenant A (the server rejects it with 422 for B):
    // B starts from the first page.
    await waitFor(() => assert.equal(listRequests.at(-1)!.cursor, undefined));
    await waitFor(() => assert.equal(button("Previous").disabled, true));
});
