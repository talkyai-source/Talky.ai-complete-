/**
 * MERGE3 Task 3.2 — a failed Do-Not-Call read uses the shared ErrorState, like
 * Campaigns, Analytics and Contacts: their sentence, the request reference and
 * "Retry", which reads the list again. Same boundary stubs as page.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { dncApi, type DncEntry } from "@/lib/dnc-api";
import { ApiClientError } from "@/lib/http-client";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: { id: "u", role: "tenant_admin", account_timezone: "Europe/London" } }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const SLOW = 15_000;
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); mock.restoreAll(); clients.splice(0).forEach((client) => client.clear()); });

const ENTRY: DncEntry = { id: "e1", normalized_number: "+447400123456", source: "caller_opt_out", reason: null, created_at: "2026-11-01T10:00:00Z", expires_at: null };

test("a failed Do-Not-Call read shows the shared error state with the reference, and Retry reads the list again", async () => {
    let fail = true;
    const list = mock.method(dncApi, "list", async () => {
        if (fail) throw new ApiClientError({ status: 500, code: "internal_error", message: "boom", requestId: "req-dnc-7", url: "http://example.test/dnc/", method: "GET" });
        return [ENTRY];
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);

    const alert = await screen.findByRole("alert", undefined, { timeout: SLOW });
    assert.equal(screen.getByText("Could not load the Do-Not-Call list.") === alert, true, "their sentence is the alert's own text");
    assert.ok(within(alert).getByRole("heading", { name: "Something went wrong" }), "the shared ErrorState");
    assert.ok(within(alert).getByText("Request reference: req-dnc-7"));
    assert.equal(screen.queryByText("boom"), null);
    assert.equal(screen.queryByRole("button", { name: "Try again" }), null);
    assert.equal(screen.queryByText("No numbers on your Do-Not-Call list yet."), null);

    fail = false;
    const before = list.mock.callCount();
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    await screen.findByText("+447400123456", undefined, { timeout: SLOW });
    assert.equal(list.mock.callCount(), before + 1);
    await waitFor(() => assert.equal(screen.queryByRole("alert"), null), { timeout: SLOW });
});
