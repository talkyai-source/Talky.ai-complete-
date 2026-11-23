import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { clearFreshLoginGrace } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

// Same boundary stubs as the rotation test: router, model picker, ws-ticket call.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, replace() {} }) };
    if (id.endsWith("assistant-model-picker")) return { AssistantModelPicker: () => null };
    if (id.includes("lib/assistant-model-api")) return { getAssistantWsToken: async () => "ticket-1" };
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
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: ((event: { code: number; reason: string }) => void) | null = null;
    constructor(public url: string) { FakeSocket.all.push(this); }
    send(data: string) { this.sent.push(data); }
    close() { this.readyState = 3; }
    serverOpen() { this.readyState = 1; this.onopen?.(); }
    serverSays(payload: Record<string, unknown>) { this.onmessage?.({ data: JSON.stringify(payload) }); }
    serverCloses(code: number, reason: string) { this.readyState = 3; this.onclose?.({ code, reason }); }
}

(window.HTMLElement.prototype as { scrollIntoView?: () => void }).scrollIntoView = () => {};

const TERMS = "Accept the current Terms and Privacy Policy to continue.";
const originalWebSocket = globalThis.WebSocket;
const originalGetMe = api.getMe;
const me = {
    id: "user-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known" as const,
    name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
    tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;

afterEach(() => {
    cleanup();
    FakeSocket.all = [];
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
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    fireEvent.click(screen.getByRole("button", { name: "Open AI assistant" }));
    await waitFor(() => assert.equal(FakeSocket.all.length, 1));
    const socket = FakeSocket.all[0];
    await act(async () => { socket.serverOpen(); });
    return socket;
}

test("a terms-acceptance refusal shows the message once and does not reconnect", async () => {
    const socket = await openAssistant();
    await act(async () => {
        socket.serverSays({ type: "error", code: "legal_acceptance_required", content: TERMS });
        socket.serverCloses(1008, "legal_acceptance_required");
    });
    // First reconnect delay is 1 s; wait well past it.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1600)); });
    assert.equal(FakeSocket.all.length, 1, "no second socket after a terms refusal");
    assert.equal(screen.getAllByText(TERMS).length, 1, "the message appears exactly once");
});

test("a refused close alone (1008 + reason, no message) does not reconnect", async () => {
    const socket = await openAssistant();
    await act(async () => { socket.serverCloses(1008, "legal_acceptance_required"); });
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 1600)); });
    assert.equal(FakeSocket.all.length, 1, "no second socket after a terms-refusal close");
});

test("control: an ordinary close (1006) still reconnects", async () => {
    const socket = await openAssistant();
    await act(async () => { socket.serverCloses(1006, ""); });
    await waitFor(() => assert.equal(FakeSocket.all.length, 2), { timeout: 4000 });
});
