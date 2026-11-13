/**
 * FX2a Task 5.1 — a malformed billing answer never prints its validation dump.
 * The minute balance shows the error state's default message under "Your
 * minute balance did not load."; the formatter shared by the plan, usage,
 * invoice and adjustment reads returns the same default.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, screen } from "@testing-library/react";
import type { QueryClient } from "@tanstack/react-query";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { z } from "zod";

import { formatBillingError } from "@/components/billing/billing-overview";
import { TopupCard } from "@/components/billing/topup-card";
import { ERROR_STATE_DEFAULT_MESSAGE, failedReadMessage } from "@/components/states/page-states";
import { AuthProvider } from "@/lib/auth-context";
import { resetBillingRefusal } from "@/lib/billing-refusal";
import { ApiClientError, clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import { renderWithQueryClient } from "@/test-utils/render";

const originalFetch = globalThis.fetch;
const clients: QueryClient[] = [];
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const ME = { id: "user-a", email: "owner@example.test", role: "owner", tenant_id: "tenant-a", minutes_remaining: 20, minutes_state: "known" };
const DUMP = /invalid_|"path"|"code"|"expected"|Invalid input/i;

afterEach(() => {
    cleanup(); globalThis.fetch = originalFetch; clients.splice(0).forEach((client) => client.clear());
    resetBillingRefusal(); window.sessionStorage.clear(); window.localStorage.clear(); invalidateRequestIdentity(); clearFreshLoginGrace();
});

function zodError(): unknown {
    try { z.object({ remaining_minutes: z.number() }).parse({ remaining_minutes: "many" }); } catch (err) { return err; }
    throw new Error("the schema accepted a malformed answer");
}

test("a malformed minute balance shows the default message under 'Your minute balance did not load.', never the validation dump", async () => {
    globalThis.fetch = async (input) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
        const path = new URL(url).pathname;
        if (path.endsWith("/auth/me")) return json(ME);
        if (path.includes("/auth/")) return json({ detail: "Not authenticated" }, 401);
        if (path.endsWith("/balance")) return json({ allocated: "a lot", remaining_minutes: "many" });
        if (path.endsWith("/packages")) return json([]);
        return json({ orders: [] });
    };
    const result = renderWithQueryClient(<AuthProvider><SearchParamsContext.Provider value={new URLSearchParams("")}><TopupCard /></SearchParamsContext.Provider></AuthProvider>);
    clients.push(result.qc);

    const heading = await screen.findByText("Your minute balance did not load.", undefined, { timeout: 15_000 });
    const box = heading.closest('[role="alert"]') as HTMLElement;
    assert.match(box.textContent ?? "", new RegExp(ERROR_STATE_DEFAULT_MESSAGE.replace(".", "\\.")));
    assert.doesNotMatch(box.textContent ?? "", DUMP);
    assert.doesNotMatch(document.body.textContent ?? "", DUMP);
});

test("the shared billing formatter (plan, usage, invoices, adjustments) returns the default message for a malformed answer", () => {
    const malformed = zodError();
    assert.match((malformed as Error).message, DUMP, "the raw message really is a validation dump");
    assert.equal(formatBillingError(malformed), "Please retry.");
    assert.equal(failedReadMessage(malformed, "The request did not complete."), ERROR_STATE_DEFAULT_MESSAGE);
});

test("the billing formatter still gives the server's own message and its fixed sentences", () => {
    assert.equal(formatBillingError(new ApiClientError({ status: 500, code: "server_error", message: "Billing store unavailable", url: "/billing/usage", method: "GET" })), "Billing store unavailable");
    assert.equal(formatBillingError(new ApiClientError({ status: 403, code: "forbidden", message: "Forbidden", url: "/billing/usage", method: "GET" })), "You do not have permission to view billing for this account.");
    assert.equal(formatBillingError("not an error"), "The request did not complete.");
});
