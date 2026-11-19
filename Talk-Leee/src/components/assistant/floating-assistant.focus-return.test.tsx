import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { clearFreshLoginGrace } from "@/lib/http-client";
import { notificationsStore } from "@/lib/notifications";

// Sign-off 1.3: Escape (and the X button) close the assistant and return focus
// to the button that opened it. Real component, real AuthProvider; the router,
// the model picker and the ws-ticket call are stubbed as in the rotation test.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, replace() {} }) };
    if (id.endsWith("assistant-model-picker")) return { AssistantModelPicker: () => null };
    if (id.includes("lib/assistant-model-api")) return { getAssistantWsToken: async () => null };
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
    readyState = 0;
    onopen: (() => void) | null = null;
    onmessage: ((event: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send() {}
    close() { this.readyState = 3; }
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
    globalThis.WebSocket = originalWebSocket;
    api.getMe = originalGetMe;
    window.localStorage.clear(); clearFreshLoginGrace(); notificationsStore.setIdentity(null);
});

const launcher = () => screen.queryByRole("button", { name: "Open AI assistant" });
const panel = () => screen.queryByRole("dialog", { name: "AI Assistant" });
const focusIsOnLauncher = () => { const button = launcher(); return button !== null && document.activeElement === button; };

async function openAssistant() {
    globalThis.WebSocket = FakeSocket as unknown as typeof WebSocket;
    window.localStorage.setItem("talklee.auth.token", "token-1");
    api.getMe = async () => ({ ...me });
    render(<AuthProvider><FloatingAssistant /></AuthProvider>);
    await waitFor(() => assert.equal(launcher() !== null, true));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    await act(async () => { launcher()!.focus(); fireEvent.click(launcher()!); });
    await waitFor(() => assert.equal(panel() !== null, true));
    // While the panel is open its button is not on the page.
    assert.equal(launcher() === null, true);
}

test("Escape closes the assistant and returns focus to the button that opened it", async () => {
    await openAssistant();
    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });
    await waitFor(() => assert.equal(panel() === null, true));
    assert.equal(focusIsOnLauncher(), true);
});

test("the Close assistant button closes it and returns focus to the button that opened it", async () => {
    await openAssistant();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Close assistant" })); });
    await waitFor(() => assert.equal(panel() === null, true));
    assert.equal(focusIsOnLauncher(), true);
});

test("a press outside the panel closes it without moving focus to the button", async () => {
    await openAssistant();
    await act(async () => { fireEvent.pointerDown(document.body); });
    await waitFor(() => assert.equal(panel() === null, true));
    assert.equal(launcher() !== null, true);
    assert.equal(focusIsOnLauncher(), false);
});
