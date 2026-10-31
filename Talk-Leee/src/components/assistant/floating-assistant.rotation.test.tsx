import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { clearFreshLoginGrace } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

// Real component, real AuthProvider. Only the router, the model picker (which
// fetches on its own) and the ws-ticket call are stubbed.
let tickets: Array<string | null> = [];
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, replace() {} }) };
    if (id.endsWith("assistant-model-picker")) return { AssistantModelPicker: () => null };
    if (id.includes("lib/assistant-model-api")) return { getAssistantWsToken: async () => (tickets.length ? tickets.shift()! : null) };
    return originalLoad.call(this, id, ...args);
};
let FloatingAssistant: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    FloatingAssistant = require("./floating-assistant").FloatingAssistant;
} finally {
    loader._load = originalLoad;
}

class FakeSocket {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0; static CLOSING = 2;
    static all: FakeSocket[] = [];
    readyState = 0;
    sent: string[] = [];
    closedByClient = false;
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    constructor(public url: string) { FakeSocket.all.push(this); }
    send(data: string) { this.sent.push(data); }
    close() { this.closedByClient = true; this.readyState = 3; }
    serverOpen() { this.readyState = 1; this.onopen?.(); }
    serverSays(payload: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(payload) }); }
}

// jsdom does not implement scrollIntoView, which the message list calls.
(window.HTMLElement.prototype as { scrollIntoView?: () => void }).scrollIntoView = () => {};

const originalWebSocket = globalThis.WebSocket;
const originalGetMe = api.getMe;
const me = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;

afterEach(() => {
    cleanup();
    FakeSocket.all = []; tickets = [];
    globalThis.WebSocket = originalWebSocket;
    api.getMe = originalGetMe;
    window.localStorage.clear(); clearFreshLoginGrace(); notificationsStore.setIdentity(null);
});

async function openAssistant() {
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    window.localStorage.setItem("talklee.auth.token", "token-1");
    api.getMe = async () => ({ ...me });
    render(<AuthProvider><FloatingAssistant /></AuthProvider>);
    await waitFor(() => assert.ok(screen.getByRole("button", { name: "Open AI assistant" })));
    // The launcher is shown for everyone; wait until the profile is loaded so the socket may open.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    fireEvent.click(screen.getByRole("button", { name: "Open AI assistant" }));
    await waitFor(() => assert.equal(FakeSocket.all.length, 1));
    const socket = FakeSocket.all[0];
    await act(async () => { socket.serverOpen(); });
    return socket;
}

test("an access-token rotation does not tear down the assistant socket or drop the in-flight reply", async () => {
    tickets = ["ticket-1"];
    const socket = await openAssistant();
    await act(async () => { socket.serverSays({ type: "assistant_typing", content: true }); });
    assert.ok(screen.getByText(/Assistant is thinking/));

    await act(async () => { api.setToken("token-2-rotated"); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 50)); });

    assert.equal(socket.closedByClient, false, "the live socket must survive a routine token rotation");
    assert.equal(FakeSocket.all.length, 1, "no replacement socket should be opened");
    await act(async () => { socket.serverSays({ type: "assistant_message", content: "Done: call placed." }); });
    assert.ok(screen.getByText("Done: call placed."));
    assert.ok(screen.queryByText(/Assistant is thinking/) === null, "typing indicator must clear once the reply arrives");
});

test("a reconnect after a rotation authenticates with the latest token, and a fresh connection clears a stuck typing indicator", async () => {
    tickets = ["ticket-1"]; // the reconnect gets no ticket, so it must fall back to the bearer token
    const socket = await openAssistant();
    await act(async () => { socket.serverSays({ type: "assistant_typing", content: true }); });
    await act(async () => { api.setToken("token-2-rotated"); });

    // The network drops the socket; the backoff reconnect (first attempt: 1 s) mints a new one.
    await act(async () => { socket.readyState = 3; socket.onclose?.(); });
    await waitFor(() => assert.equal(FakeSocket.all.length, 2), { timeout: 4000 });
    const next = FakeSocket.all[1];
    await act(async () => { next.serverOpen(); });
    assert.deepEqual(next.sent.map(frame => JSON.parse(frame)), [{ type: "auth", token: "token-2-rotated" }]);

    await act(async () => { next.serverSays({ type: "connected", conversation_id: "new" }); });
    assert.ok(screen.queryByText(/Assistant is thinking/) === null, "the previous turn's typing indicator must not outlive its socket");
});
