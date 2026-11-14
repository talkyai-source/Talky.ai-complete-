/**
 * FX2a Task 4.1 + 4.3 — Connectors: a failed status read shows the shared
 * error state with Retry and the server's message; an unexpected answer shows
 * the error state's default message, never the validation dump; the connector
 * return result is announced (role="status" on success, role="alert" on failure).
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { backendApi } from "@/lib/backend-api";
import { ApiClientError } from "@/lib/http-client";
import { ConnectorProviderStatusSchema, ListResponseSchema } from "@/lib/models";
import { renderWithQueryClient } from "@/test-utils/render";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {} };
let query = new URLSearchParams("");
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => router, useSearchParams: () => query, useParams: () => ({ type: "calendar" }) };
    if (id === "next/link") {
        return { __esModule: true, default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> };
    }
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/connectors/salesforce-settings")) return { SalesforceSettingsPanel: () => null };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
let ReturnPage: React.ComponentType;
let TypedReturnPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    ReturnPage = require("./callback/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    TypedReturnPage = require("./[type]/callback/page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 20_000;
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const realClose = window.close;

afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.close = realClose;
    query = new URLSearchParams("");
    for (const store of [window.sessionStorage, window.localStorage]) {
        for (const key of ["connectors.callback.sent", "connectors.callback.eventId", "connectors.refresh", "connectors.refresh.event"]) store.removeItem(key);
    }
});

for (const [status, message] of [[500, "Internal server error"], [403, "Forbidden"]] as const) {
    test(`a failed status read (${status}) shows the server's message in the shared error state, and Retry reads the status again`, async () => {
        let fail = true;
        const read = mock.method(backendApi.connectors, "status", async () => {
            if (fail) throw new ApiClientError({ status, code: status === 403 ? "forbidden" : "server_error", message, url: "/connectors/status", method: "GET" });
            return { items: [{ type: "calendar" as const, status: "connected" as const, provider: "google_calendar" }] };
        });
        renderWithQueryClient(<Page />);
        await waitFor(() => assert.equal(shown(message), true), { timeout: SLOW });
        assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
        assert.equal(screen.getByText(message).getAttribute("role"), "alert", "the failure is announced");

        fail = false;
        const before = read.mock.callCount();
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
        await waitFor(() => assert.equal(screen.queryByLabelText("Status: Connected") !== null, true), { timeout: SLOW });
        assert.equal(read.mock.callCount() > before, true, "Retry sent the request again");
        assert.equal(shown(message), false);
    });
}

test("an unexpected status answer shows the error state's default message, never the validation dump", async () => {
    mock.method(backendApi.connectors, "status", async () => ListResponseSchema(ConnectorProviderStatusSchema).parse({ items: [{ type: 7, status: "sideways" }] }));
    renderWithQueryClient(<Page />);
    await waitFor(() => assert.equal(shown("Please retry."), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.doesNotMatch(document.body.textContent ?? "", /invalid_|"path"|"code"|expected|Invalid input|Invalid option/i);
});

for (const [route, page] of [["/connectors/callback", () => ReturnPage], ["/connectors/[type]/callback", () => TypedReturnPage]] as const) {
    test(`${route}: a failed return is announced with role=alert and keeps its sentence`, () => {
        window.close = () => {};
        query = new URLSearchParams("status=error&type=calendar&error=access_denied");
        render(React.createElement(page()));
        assert.equal(screen.getByText("Access wasn't granted. Try again and allow access.").getAttribute("role"), "alert");
    });

    test(`${route}: a successful return is announced with role=status and keeps its sentence`, () => {
        window.close = () => {};
        query = new URLSearchParams("status=success&type=calendar&provider=google_calendar");
        render(React.createElement(page()));
        assert.equal(screen.getByText("Google Calendar connected successfully.").getAttribute("role"), "status");
    });
}
