import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { backendApi } from "@/lib/backend-api";
import type { ConnectorProviderStatus } from "@/lib/models";
import { renderWithQueryClient } from "@/test-utils/render";

// Real page, real cards and the real status hook. Only layout, routing and the
// Salesforce settings panel (which fetches on its own) are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {} };
let searchParams = new URLSearchParams("");
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => router, useSearchParams: () => searchParams };
    if (id === "next/link") {
        return {
            __esModule: true,
            default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
        };
    }
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/connectors/salesforce-settings")) return { SalesforceSettingsPanel: () => null };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
let ReturnPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    ReturnPage = require("./callback/page").default;
} finally {
    loader._load = originalLoad;
}

// A failing assert.equal(<DOM node>, null) makes node:assert print the whole
// JSDOM node and never return, so a regression would hang instead of fail.
const absent = (node: unknown, message?: string) => assert.equal(node === null, true, message ?? "expected nothing to be rendered");

// Generous: the full suite runs many files in parallel on a loaded machine.
const SLOW = 20_000;
const CARDS = ["calendar", "email", "crm", "drive", "salesforce"] as const;
const CHECKING = "Checking status…";
const ACCESS_DENIED = "Access wasn't granted. Try again and allow access.";

afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.localStorage.removeItem("connectors.refresh.event");
});

const card = (type: string) => within(screen.getByTestId(`connector-card-${type}`));
const pause = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

function postFromOrigin(origin: string, data: unknown) {
    return act(async () => {
        window.dispatchEvent(new window.MessageEvent("message", { data, origin }));
    });
}

const ANSWER: ConnectorProviderStatus[] = [
    { type: "calendar", status: "connected", provider: "google_calendar", last_sync: "2026-01-05T09:30:00Z" },
    { type: "email", status: "disconnected", provider: null },
    { type: "crm", status: "error", provider: "hubspot" },
    { type: "drive", status: "expired", provider: "google_drive" },
];

test("until the status call answers, every card shows 'Checking status…' with no status and no buttons", async () => {
    let answer: (value: { items: ConnectorProviderStatus[] }) => void = () => {};
    mock.method(backendApi.connectors, "status", () => new Promise((resolve) => { answer = resolve; }));
    renderWithQueryClient(<Page />);

    for (const type of CARDS) {
        assert.ok(card(type).getByText(CHECKING), `${type} is checking`);
        absent(card(type).queryByLabelText(/^Status:/), `${type} shows no status yet`);
    }
    assert.equal(screen.queryAllByRole("button").length, 0, "no Connect, Reconnect or Disconnect before the answer");
    absent(screen.queryByText(/not enabled by this server/), "Salesforce is not called unavailable before the answer");

    await act(async () => { answer({ items: ANSWER }); });
    await waitFor(() => assert.ok(card("calendar").getByLabelText("Status: Connected")), { timeout: SLOW });
    absent(screen.queryByText(CHECKING));
    assert.ok(card("email").getByLabelText("Status: Disconnected"));
    assert.ok(card("email").getByRole("button", { name: "Connect" }));
    assert.ok(card("crm").getByLabelText("Status: Needs attention"));
    assert.ok(card("crm").getByRole("button", { name: "Reconnect" }));
    assert.ok(card("drive").getByLabelText("Status: Expired"));
    assert.ok(card("drive").getByText("Credentials have expired. Reconnect to refresh access."));
    assert.ok(card("calendar").getByRole("button", { name: "Disconnect" }));
    // The backend did not advertise Salesforce: unchanged behaviour, from a real answer.
    assert.ok(card("salesforce").getByLabelText("Status: Unavailable"));
});

test("a failed status call leaves the error box as the only message: no status and no buttons on any card", async () => {
    const status = mock.method(backendApi.connectors, "status", async () => {
        throw new Error("Connector status is temporarily unavailable");
    });
    renderWithQueryClient(<Page />);

    // The wait ends: the request errors (after the hook's two retries).
    await waitFor(() => assert.ok(screen.getByText("Connector status is temporarily unavailable")), { timeout: SLOW });
    assert.equal(status.mock.callCount() >= 3, true);
    for (const type of CARDS) {
        absent(card(type).queryByLabelText(/^Status:/), `${type} shows no status`);
        absent(card(type).queryByText(CHECKING), `${type} is no longer checking`);
        absent(card(type).queryByText("Not connected."));
    }
    assert.equal(screen.queryAllByRole("button").length, 0);
    absent(screen.queryByText(/not enabled by this server/));
});

test("a connector missing from a real answer gets no status and no buttons", async () => {
    mock.method(backendApi.connectors, "status", async () => ({ items: ANSWER.filter((x) => x.type !== "drive") }));
    renderWithQueryClient(<Page />);

    await waitFor(() => assert.ok(card("calendar").getByLabelText("Status: Connected")), { timeout: SLOW });
    absent(card("drive").queryByLabelText(/^Status:/));
    assert.equal(card("drive").queryAllByRole("button").length, 0);
    absent(card("drive").queryByText(CHECKING));
});

test("a return message is heard only from this origin, shows on the matching card and never sets a status", async () => {
    const status = mock.method(backendApi.connectors, "status", async () => ({ items: ANSWER }));
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.ok(card("email").getByLabelText("Status: Disconnected")), { timeout: SLOW });
    await pause(700);
    const before = status.mock.callCount();

    const failure = { type: "connectors:updated", eventId: "evt-1", ok: false, providerType: "email", message: ACCESS_DENIED };
    await postFromOrigin("https://evil.example", failure);
    await pause(300);
    absent(screen.queryByText(ACCESS_DENIED), "a foreign origin is ignored");
    assert.equal(status.mock.callCount(), before, "a foreign origin does not even trigger a refetch");

    await postFromOrigin(window.location.origin, failure);
    assert.ok(card("email").getByText(ACCESS_DENIED));
    for (const type of CARDS) {
        if (type !== "email") absent(card(type).queryByText(ACCESS_DENIED), `${type} is not the matching card`);
    }
    await waitFor(() => assert.equal(status.mock.callCount() > before, true, "status is refetched"), { timeout: SLOW });

    // A message claiming success changes nothing by itself: the backend still says disconnected.
    await postFromOrigin(window.location.origin, { type: "connectors:updated", eventId: "evt-2", ok: true, providerType: "email", message: "email connected successfully." });
    await pause(300);
    assert.ok(card("email").getByLabelText("Status: Disconnected"));
    assert.ok(card("email").getByRole("button", { name: "Connect" }));
    absent(card("email").queryByLabelText("Status: Connected"));
});

test("a failure return that names no connector shows only on the card that opened the window", async () => {
    mock.method(backendApi.connectors, "status", async () => ({ items: ANSWER }));
    mock.method(backendApi.connectors, "authorize", async () => ({ authorization_url: "https://provider.example/auth" }));
    const prevOpen = window.open;
    window.open = (() => ({ closed: false, focus() {}, location: { href: "about:blank" } })) as unknown as typeof window.open;
    try {
        renderWithQueryClient(<Page />);
        await waitFor(() => assert.ok(card("email").getByLabelText("Status: Disconnected")), { timeout: SLOW });

        // The backend's access_denied return carries no type (connectors.py:722).
        const unnamed = { type: "connectors:updated", ok: false, message: ACCESS_DENIED };
        await postFromOrigin(window.location.origin, { ...unnamed, eventId: "evt-a" });
        absent(screen.queryByText(ACCESS_DENIED), "no card opened a window, so no card claims it");

        await act(async () => { fireEvent.click(card("email").getByRole("button", { name: "Connect" })); });
        await pause(100);
        await postFromOrigin(window.location.origin, { ...unnamed, eventId: "evt-b" });
        assert.ok(card("email").getByText(ACCESS_DENIED));
        for (const type of CARDS) {
            if (type !== "email") absent(card(type).queryByText(ACCESS_DENIED), `${type} did not open the window`);
        }
        assert.ok(card("email").getByLabelText("Status: Disconnected"));
    } finally {
        window.open = prevOpen;
    }
});

test("loading the Connectors page clears the return page's 'already sent' mark so a later popup cannot inherit it", async () => {
    window.sessionStorage.setItem("connectors.callback.sent", "1");
    window.sessionStorage.setItem("connectors.unrelated", "keep");
    mock.method(backendApi.connectors, "status", async () => ({ items: ANSWER }));
    try {
        renderWithQueryClient(<Page />);
        await waitFor(() => assert.ok(card("email").getByLabelText("Status: Disconnected")), { timeout: SLOW });
        assert.equal(window.sessionStorage.getItem("connectors.callback.sent"), null);
        assert.equal(window.sessionStorage.getItem("connectors.unrelated"), "keep", "only that key is cleared");
    } finally {
        window.sessionStorage.removeItem("connectors.callback.sent");
        window.sessionStorage.removeItem("connectors.unrelated");
    }
});

test("a tab that once showed a return page still shows the returns of the popups it opens later", async () => {
    // jsdom has one sessionStorage. It stands in for this tab's storage and for
    // the copy each popup starts with (a popup inherits its opener's sessionStorage).
    const PERMISSIONS = "Not all permissions were granted. Reconnect and allow everything requested.";
    const status = mock.method(backendApi.connectors, "status", async () => ({ items: ANSWER }));
    // Node's BroadcastChannel cannot deliver into jsdom's event classes (it throws
    // inside Node). Both pages treat a missing BroadcastChannel as "use storage",
    // which is the path a popup without an opener relies on anyway.
    const globals = globalThis as { BroadcastChannel?: unknown };
    const realBroadcastChannel = globals.BroadcastChannel;
    globals.BroadcastChannel = undefined;
    try {
        // 1. The tab itself shows a return page (the popup-blocked fallback runs the flow in the tab).
        searchParams = new URLSearchParams("status=error&type=email&error=access_denied");
        const inTab = render(<ReturnPage />);
        await pause(50);
        inTab.unmount();
        assert.equal(typeof window.sessionStorage.getItem("connectors.callback.sent"), "string", "the tab showed a return page");

        // 2. The same tab opens Connectors. Its old return is not news (judgment call 5).
        searchParams = new URLSearchParams("");
        renderWithQueryClient(<Page />);
        await waitFor(() => assert.ok(card("email").getByLabelText("Status: Disconnected")), { timeout: SLOW });
        await pause(900);
        absent(card("email").queryByText(ACCESS_DENIED), "the earlier return is not shown again");

        // 3. A new Connect: its popup starts from this tab's sessionStorage and lands on the return page.
        const before = status.mock.callCount();
        searchParams = new URLSearchParams("status=error&type=email&error=insufficient_scope");
        const popup1 = render(<ReturnPage />);
        await pause(900);
        assert.equal(card("email").queryByText(PERMISSIONS) !== null, true, "the new return was dropped as a duplicate of the old one");
        assert.equal(status.mock.callCount() > before, true, "status is refetched");
        popup1.unmount();

        // 4. Reconnect: another popup, again starting from the tab's storage (the tab never set the mark again).
        window.sessionStorage.removeItem("connectors.callback.sent");
        searchParams = new URLSearchParams("status=error&type=email&error=access_denied");
        const popup2 = render(<ReturnPage />);
        await pause(900);
        assert.equal(card("email").queryByText(ACCESS_DENIED) !== null, true, "the second return was dropped as a duplicate of the first");
        popup2.unmount();
    } finally {
        globals.BroadcastChannel = realBroadcastChannel;
        searchParams = new URLSearchParams("");
        window.sessionStorage.removeItem("connectors.callback.sent");
        window.sessionStorage.removeItem("connectors.callback.eventId");
    }
});

test("a return event left in storage by an earlier visit refetches but is not shown again", async () => {
    window.localStorage.setItem(
        "connectors.refresh.event",
        JSON.stringify({ type: "connectors:updated", eventId: "evt-old", ok: false, providerType: "email", message: ACCESS_DENIED, ts: 1 })
    );
    const status = mock.method(backendApi.connectors, "status", async () => ({ items: ANSWER }));
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.ok(card("email").getByLabelText("Status: Disconnected")), { timeout: SLOW });
    await pause(900);
    absent(screen.queryByText(ACCESS_DENIED));
    assert.equal(status.mock.callCount() >= 2, true, "the stored event still triggers the refetch, as before");
});
