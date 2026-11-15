/**
 * FX6a Task 2 (plan 2.5: "Google is hidden or disabled if OAuth isn't ready.").
 * GET /connectors/status says per card whether the server can run its sign-in
 * (`available`) and why not (`unavailable_reason`). An unavailable card shows
 * the existing "Unavailable" badge and the server's reason, with no Connect;
 * a connected card that became unavailable keeps Disconnect; the Salesforce
 * card is shown only when the server returns its row.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { backendApi } from "@/lib/backend-api";
import { ConnectorProviderStatusSchema, type ConnectorProviderStatus } from "@/lib/models";
import { renderWithQueryClient } from "@/test-utils/render";

// Real page, real cards and the real status hook. Only layout, routing and the
// Salesforce settings panel (which fetches on its own) are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => router, useSearchParams: () => new URLSearchParams("") };
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
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 20_000;
// The server's one reason (backend/app/api/v1/endpoints/connectors.py:379).
const REASON = "This connector isn't available yet. Contact support to turn it on.";
// The text the page used to write itself for a missing Salesforce row.
const OLD_SALESFORCE_TEXT = /SALESFORCE_CLIENT_ID|not enabled by this server/;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.localStorage.removeItem("connectors.refresh.event");
});

const cardNode = (type: string) => screen.queryByTestId(`connector-card-${type}`);
const card = (type: string) => within(screen.getByTestId(`connector-card-${type}`));
const buttons = (type: string) => card(type).queryAllByRole("button").map((button) => button.textContent?.trim());
const off = { available: false, unavailable_reason: REASON };
const on = { available: true, unavailable_reason: null };

async function show(items: ConnectorProviderStatus[]) {
    mock.method(backendApi.connectors, "status", async () => ({ items }));
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(card("calendar").queryByLabelText(/^Status:/) !== null, true), { timeout: SLOW });
}

test("the status answer keeps available and unavailable_reason", () => {
    const parsed = ConnectorProviderStatusSchema.parse({ type: "calendar", status: "disconnected", provider: null, ...off });
    assert.equal(parsed.available, false);
    assert.equal(parsed.unavailable_reason, REASON);
    const older = ConnectorProviderStatusSchema.parse({ type: "calendar", status: "disconnected" });
    assert.equal(older.available, undefined, "an answer without the field still parses");
});

test("Google Calendar and HubSpot unavailable: the Unavailable badge, the server's reason and no Connect", async () => {
    await show([
        { type: "calendar", status: "disconnected", provider: null, ...off },
        { type: "email", status: "disconnected", provider: null, ...on },
        { type: "crm", status: "disconnected", provider: null, ...off },
        { type: "drive", status: "disconnected", provider: null, ...on },
    ]);
    for (const type of ["calendar", "crm"]) {
        assert.equal(card(type).queryByLabelText("Status: Unavailable") !== null, true, `${type} carries the Unavailable badge`);
        assert.equal(card(type).queryAllByText(REASON).length, 1, `${type} shows the server's reason once`);
        assert.deepEqual(buttons(type), [], `${type} offers no Connect`);
    }
    for (const type of ["email", "drive"]) {
        assert.equal(card(type).queryByLabelText("Status: Disconnected") !== null, true, `${type} is unchanged`);
        assert.deepEqual(buttons(type), ["Connect"], `${type} still offers Connect`);
        assert.equal(card(type).queryAllByText(REASON).length, 0);
    }
});

test("Gmail and Google Drive follow the same rule", async () => {
    await show([
        { type: "calendar", status: "disconnected", provider: null, ...on },
        { type: "email", status: "disconnected", provider: null, ...off },
        { type: "crm", status: "disconnected", provider: null, ...on },
        { type: "drive", status: "disconnected", provider: null, ...off },
    ]);
    for (const type of ["email", "drive"]) {
        assert.equal(card(type).queryByLabelText("Status: Unavailable") !== null, true);
        assert.equal(card(type).queryAllByText(REASON).length, 1);
        assert.deepEqual(buttons(type), []);
    }
    assert.deepEqual(buttons("calendar"), ["Connect"]);
});

test("an answer without the availability fields leaves every card available", async () => {
    await show([
        { type: "calendar", status: "disconnected", provider: null },
        { type: "email", status: "disconnected", provider: null },
        { type: "crm", status: "disconnected", provider: null },
        { type: "drive", status: "disconnected", provider: null },
    ]);
    for (const type of ["calendar", "email", "crm", "drive"]) {
        assert.deepEqual(buttons(type), ["Connect"], `${type} offers Connect`);
        assert.equal(card(type).queryByLabelText("Status: Unavailable") === null, true);
    }
});

test("a connected card that became unavailable keeps Disconnect and offers no Connect or Reconnect", async () => {
    await show([
        { type: "calendar", status: "connected", provider: "google_calendar", ...off },
        { type: "email", status: "disconnected", provider: null, ...on },
        { type: "crm", status: "error", provider: "hubspot", ...off },
        { type: "drive", status: "expired", provider: "google_drive", ...off },
    ]);
    for (const type of ["calendar", "crm", "drive"]) {
        assert.deepEqual(buttons(type), ["Disconnect"], `${type} keeps Disconnect only`);
        assert.equal(card(type).queryByLabelText("Status: Unavailable") !== null, true);
        assert.equal(card(type).queryAllByText(REASON).length, 1);
    }
});

test("without the Salesforce row the Salesforce card is not shown, and the old text is gone", async () => {
    await show([
        { type: "calendar", status: "disconnected", provider: null, ...on },
        { type: "email", status: "disconnected", provider: null, ...on },
        { type: "crm", status: "disconnected", provider: null, ...on },
        { type: "drive", status: "disconnected", provider: null, ...on },
    ]);
    assert.equal(cardNode("salesforce") === null, true, "no Salesforce card");
    assert.equal(screen.queryByText(OLD_SALESFORCE_TEXT) === null, true, "no text naming server settings");
    assert.equal(screen.queryByText("Server capability required") === null, true);
    for (const type of ["calendar", "email", "crm", "drive"]) assert.equal(cardNode(type) !== null, true, `${type} card stays`);
});

test("with the Salesforce row the Salesforce card is shown and can be connected", async () => {
    await show([
        { type: "calendar", status: "disconnected", provider: null, ...on },
        { type: "email", status: "disconnected", provider: null, ...on },
        { type: "crm", status: "disconnected", provider: null, ...on },
        { type: "drive", status: "disconnected", provider: null, ...on },
        { type: "salesforce", status: "disconnected", provider: null, ...on },
    ]);
    assert.equal(cardNode("salesforce") !== null, true);
    assert.deepEqual(buttons("salesforce"), ["Connect"]);
    assert.equal(screen.queryByText(OLD_SALESFORCE_TEXT) === null, true);
});

test("a Salesforce row the server marks unavailable shows the server's reason, not the old text", async () => {
    await show([
        { type: "calendar", status: "disconnected", provider: null, ...on },
        { type: "email", status: "disconnected", provider: null, ...on },
        { type: "crm", status: "disconnected", provider: null, ...on },
        { type: "drive", status: "disconnected", provider: null, ...on },
        { type: "salesforce", status: "connected", provider: "salesforce", ...off },
    ]);
    assert.equal(card("salesforce").queryAllByText(REASON).length, 1);
    assert.deepEqual(buttons("salesforce"), ["Disconnect"]);
    assert.equal(screen.queryByText(OLD_SALESFORCE_TEXT) === null, true);
});
