import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { act, cleanup } from "@testing-library/react";
import { renderWithQueryClient } from "@/test-utils/render";
import { getSupportStatus, resetSupportStatusForTests } from "@/lib/support-status-store";
import { supportChatStore } from "@/lib/support-chat-store";

/*
 * The shipping gate, end to end through the real launcher: GET /support/config
 * answering with an error, a 404 or enabled=false renders no Help button,
 * publishes no sidebar entry and never polls /support/unread.
 */

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let authStatus: "authenticated" | "anonymous" = "authenticated";
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/dashboard", useRouter: () => ({ push() {}, replace() {}, prefetch() {} }) };
    if (id.includes("lib/auth-context")) {
        return {
            useAuth: () => ({
                user: authStatus === "authenticated" ? { id: "user-1", tenant_id: "tenant-1", email: "a@b.c", role: "user" } : null,
                status: authStatus, loading: false,
            }),
        };
    }
    if (id.includes("lib/notifications-client")) return { useNotificationsActions: () => ({ create: () => null }) };
    return originalLoad.call(this, id, ...args);
};
let SupportLauncher: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    SupportLauncher = require("./support-launcher").SupportLauncher;
} finally {
    loader._load = originalLoad;
}

const enabledConfig = {
    enabled: true, bot_enabled: true, permissions: { report: true, read_team: false },
    categories: [{ id: "other", label: "Something else" }], suggestions: [],
    attachments: { max_mb: 10, max_per_message: 5, types: ["png"] },
};

let calls: string[] = [];
let configResponse: () => Response;
const previousFetch = globalThis.fetch;

beforeEach(() => {
    calls = [];
    authStatus = "authenticated";
    resetSupportStatusForTests();
    supportChatStore.resetForTests();
    globalThis.fetch = (async (url: RequestInfo | URL) => {
        const path = String(url);
        calls.push(path);
        if (path.includes("/support/config")) return configResponse();
        if (path.includes("/support/unread")) return new Response(JSON.stringify({ count: 2, ticket_ids: ["t1", "t2"] }), { status: 200, headers: { "content-type": "application/json" } });
        if (path.includes("/support/tickets")) return new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200, headers: { "content-type": "application/json" } });
        return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
    }) as typeof fetch;
});
afterEach(() => {
    cleanup();
    globalThis.fetch = previousFetch;
});

async function settle() {
    for (let round = 0; round < 6; round += 1) {
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)); });
    }
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

for (const [name, response] of [
    ["a server error", () => json({ error: { code: "support_unavailable", message: "down", details: null, request_id: "r" } }, 503)],
    ["a 404", () => json({ error: { code: "not_found", message: "Not found.", details: null, request_id: "r" } }, 404)],
    ["enabled=false", () => json({ ...enabledConfig, enabled: false, permissions: { report: false, read_team: false }, categories: [], attachments: { max_mb: 0, max_per_message: 0, types: [] } })],
] as const) {
    test(`config answering with ${name}: no Help button, no sidebar entry, no unread query`, async () => {
        configResponse = response;
        renderWithQueryClient(<SupportLauncher />);
        await settle();
        assert.equal(document.querySelector("[data-support-help-button]"), null, "no Help button");
        assert.deepEqual(getSupportStatus(), { visible: false, unreadCount: 0 }, "nothing published for the sidebar");
        assert.equal(calls.filter((path) => path.includes("/support/unread")).length, 0, "no unread polling");
        assert.equal(calls.filter((path) => path.includes("/support/config")).length, 1, "config asked once");
    });
}

test("config enabled with report permission: the Help button, the sidebar entry and the unread count appear", async () => {
    configResponse = () => json(enabledConfig);
    renderWithQueryClient(<SupportLauncher />);
    await settle();
    const button = document.querySelector<HTMLButtonElement>("[data-support-help-button]");
    assert.ok(button, "Help button rendered");
    assert.equal(button.getAttribute("aria-label"), "Help");
    assert.equal(button.getAttribute("aria-expanded"), "false");
    assert.equal(calls.filter((path) => path.includes("/support/unread")).length, 1, "unread polled once");
    assert.deepEqual(getSupportStatus(), { visible: true, unreadCount: 2 });
});

test("config enabled without the report permission renders nothing", async () => {
    configResponse = () => json({ ...enabledConfig, permissions: { report: false, read_team: true } });
    renderWithQueryClient(<SupportLauncher />);
    await settle();
    assert.equal(document.querySelector("[data-support-help-button]"), null);
    assert.equal(getSupportStatus().visible, false);
    assert.equal(calls.filter((path) => path.includes("/support/unread")).length, 0);
});

test("an anonymous visitor never asks for the config", async () => {
    authStatus = "anonymous";
    configResponse = () => json(enabledConfig);
    renderWithQueryClient(<SupportLauncher />);
    await settle();
    assert.equal(calls.length, 0);
    assert.equal(document.querySelector("[data-support-help-button]"), null);
});
