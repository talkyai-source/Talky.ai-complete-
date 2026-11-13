/**
 * FX2b4 — only a server's own message is shown as is. The app's own
 * validation sentences, a JavaScript error and a network failure show
 * "Please retry."; a Settings read answering {} is a failed read with Retry;
 * the route error pages follow the same rule.
 */
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import AssistantActionsError from "@/app/assistant/actions/error";
import AssistantError from "@/app/assistant/error";
import ConnectorsError from "@/app/connectors/error";
import ContactsError from "@/app/contacts/error";
import MeetingsError from "@/app/meetings/error";
import RemindersError from "@/app/reminders/error";
import SupportError from "@/app/support/error";
import SupportTicketError from "@/app/support/tickets/[id]/error";
import SupportTicketsError from "@/app/support/tickets/error";
import DeviceList from "@/components/auth/device-list";
import { formatBillingError } from "@/components/billing/billing-overview";
import { RecordingPolicySection } from "@/components/settings/recording-policy-section";
import { failedReadMessage } from "@/components/states/page-states";
import { api, sharedHttpClient } from "@/lib/api";
import { ApiClientError, setRequestIdentity, setTokenProvider } from "@/lib/http-client";
import { inboundApi } from "@/lib/inbound-api";
import { supportApi } from "@/lib/support-api";
import { UnexpectedAnswerError } from "@/lib/unexpected-answer";

const SLOW = 15_000;
const realFetch = globalThis.fetch;
afterEach(() => { cleanup(); mock.restoreAll(); globalThis.fetch = realFetch; setTokenProvider(null); });
const shown = (text: string | RegExp) => screen.queryAllByText(text).length > 0;
const caught = async (run: () => Promise<unknown>): Promise<unknown> => {
    try { await run(); } catch (err) { return err; }
    throw new Error("expected the read to fail");
};
const clientError = (code: string, message: string, status?: number) => new ApiClientError({ code, message, status, url: "/x", method: "GET" });
function jsError(): Error {
    try { (undefined as unknown as unknown[]).map(() => null); } catch (err) { return err as Error; }
    throw new Error("expected a TypeError");
}

// ───────── Task 1 ─────────

test("failedReadMessage: the app's own validation sentence gives 'Please retry.'; a server message is kept", () => {
    assert.equal(failedReadMessage(clientError("invalid_response", "Invalid response format")), "Please retry.");
    assert.equal(failedReadMessage(new UnexpectedAnswerError("Inbound campaign response is missing an id.")), "Please retry.");
    assert.equal(failedReadMessage(clientError("server_error", "The list is being rebuilt.", 503)), "The list is being rebuilt.");
});

test("inbound campaign: an answer without an id is the app's validation failure, shown as 'Please retry.'", async () => {
    mock.method(sharedHttpClient(), "request", async () => ({}));
    const err = await caught(() => inboundApi.get("inb-1"));
    assert.equal((err as Error).message, "Inbound campaign response is missing an id.", "the sentence is kept for logs");
    assert.equal(failedReadMessage(err, "This campaign may have been removed or you may not have access."), "Please retry.");
});

test("inbound list: an answer in an invalid format is the app's validation failure, shown as 'Please retry.'", async () => {
    mock.method(sharedHttpClient(), "request", async () => ({}));
    const err = await caught(() => inboundApi.list());
    assert.equal((err as Error).message, "Inbound campaign list response has an invalid format.");
    assert.equal(failedReadMessage(err, "The campaign list could not be loaded."), "Please retry.");
});

test("support: an unexpected answer is the app's validation failure, shown as 'Please retry.'", async () => {
    mock.method(sharedHttpClient(), "request", async () => ({}));
    const err = await caught(() => supportApi.getTicket("t-1"));
    assert.match((err as Error).message, /^Unexpected .* response from the server\.$/);
    assert.equal(failedReadMessage(err), "Please retry.");
});

test("billing: a network failure or the app's validation sentence gives 'Please retry.'; server and permission messages are kept", () => {
    assert.equal(formatBillingError(clientError("network_error", "Failed to fetch")), "Please retry.");
    assert.equal(formatBillingError(clientError("invalid_response", "Invalid response format")), "Please retry.");
    assert.equal(formatBillingError(clientError("server_error", "Billing is being reconciled.", 503)), "Billing is being reconciled.");
    assert.equal(formatBillingError(clientError("forbidden", "Forbidden", 403)), "You do not have permission to view billing for this account.");
});

// ───────── Task 2 ─────────

test("Settings devices: a sessions answer of {} is a failed read with Retry, never 'No active devices'", async () => {
    type Sessions = Awaited<ReturnType<typeof api.getActiveSessions>>;
    let answer = {} as Sessions;
    const read = mock.method(api, "getActiveSessions", async () => answer);
    render(<DeviceList token="" />);
    await waitFor(() => assert.equal(shown("Failed to fetch devices"), true), { timeout: SLOW });
    assert.equal(shown("No active devices"), false);
    answer = { sessions: [], total: 0 };
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(shown("No active devices"), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2);
});

test("Settings recording: a policy answer of {} is a failed read with Retry, never a form", async () => {
    setTokenProvider(() => null);
    setRequestIdentity({ userId: "user-a", tenantId: "tenant-a" });
    let reads = 0;
    globalThis.fetch = (async () => { reads += 1; return Response.json({}); }) as typeof fetch;
    render(<RecordingPolicySection canEdit />);
    await waitFor(() => assert.equal(shown("Request failed"), true), { timeout: SLOW });
    assert.equal(screen.queryByTestId("recording-policy") === null, true, "no policy card or form");
    const before = reads;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(reads > before, true), { timeout: SLOW });
});

// ───────── Task 3: the route error pages ─────────

const ROUTE_ERRORS: Array<[string, React.ComponentType<{ error: Error & { digest?: string }; reset: () => void }>]> = [
    ["assistant/actions", AssistantActionsError],
    ["assistant", AssistantError],
    ["connectors", ConnectorsError],
    ["contacts", ContactsError],
    ["meetings", MeetingsError],
    ["reminders", RemindersError],
    ["support", SupportError],
    ["support/tickets/[id]", SupportTicketError],
    ["support/tickets", SupportTicketsError],
];
for (const [route, ErrorPage] of ROUTE_ERRORS) {
    test(`route error page ${route}: a JavaScript error shows 'Please retry.', never the raw text; Retry resets`, () => {
        let resets = 0;
        render(<ErrorPage error={jsError()} reset={() => { resets += 1; }} />);
        assert.equal(shown("Please retry."), true);
        assert.equal(shown(/Cannot read properties/), false);
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
        assert.equal(resets, 1);
    });
}
