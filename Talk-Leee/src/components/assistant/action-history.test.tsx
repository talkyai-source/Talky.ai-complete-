import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { backendApi } from "@/lib/backend-api";
import { AssistantRunSchema } from "@/lib/models";
import { ActionHistory, actionOutcome } from "./action-history";

const originalList = backendApi.assistantRuns.list;
afterEach(() => { cleanup(); backendApi.assistantRuns.list = originalList; });

function show() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } });
    render(<QueryClientProvider client={client}><ActionHistory /></QueryClientProvider>);
}

test("saved unknown actions expose their reference and preserve status filters without a resend control", async () => {
    const queries: Array<Parameters<typeof originalList>[0]> = [];
    backendApi.assistantRuns.list = async (query) => {
        queries.push(query);
        return { items: [AssistantRunSchema.parse({ id: "owned-action", type: "send_email", triggered_by: "assistant_tool", status: "unknown",
            created_at: "2026-10-05T00:00:00Z", confirmation_allowed: false,
            receipt: { external_account_id: "original-account", message_id: "original-provider-message" } })], total: 1, page: query.page, page_size: 50 };
    };
    show();
    await screen.findByRole("button", { name: "View record" });
    assert.equal(screen.queryByRole("button", { name: /^retry|execute|plan/i }), null);
    fireEvent.click(screen.getByRole("button", { name: "View record" }));
    assert.ok(screen.getByText("owned-action"));
    assert.ok(screen.getByText("original-account"));
    assert.ok(screen.getByText("original-provider-message"));
    assert.ok(screen.getByText(/Do not resend this action/));
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.change(screen.getByRole("combobox", { name: "Action status" }), { target: { value: "unknown" } });
    await waitFor(() => assert.deepEqual(queries.at(-1)?.statuses, ["unknown"]));
    assert.equal(queries.at(-1)?.page, 1);
});

test("an unavailable history does not imply failed execution or an empty successful result", async () => {
    backendApi.assistantRuns.list = async () => { throw new Error("Saved records are unavailable."); };
    show();
    assert.match((await screen.findByRole("alert")).textContent ?? "", /does not tell us whether an action ran/);
    assert.equal(screen.queryByText(/No saved actions match/), null);
    assert.equal(screen.queryByRole("button", { name: /^retry$/i }), null);
});

test("pagination follows the server total and does not claim a whole-history export", async () => {
    const queries: number[] = [];
    backendApi.assistantRuns.list = async (query) => {
        queries.push(query.page ?? 1);
        return { items: [AssistantRunSchema.parse({ id: `record-${query.page}`, type: "update_campaign_config", triggered_by: "assistant_tool",
            status: "completed", created_at: "2026-10-05T00:00:00Z" })], total: 51, page: query.page, page_size: 50 };
    };
    show();
    await screen.findByRole("button", { name: "View record" });
    assert.ok(screen.getByRole("button", { name: "Export page CSV" }));
    await waitFor(() => assert.equal(screen.getByRole("button", { name: "Next" }).hasAttribute("disabled"), false));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => assert.equal(queries.at(-1), 2));
    await waitFor(() => assert.equal(screen.getByRole("button", { name: "Next" }).hasAttribute("disabled"), true));
});

test("a completed callback receipt only proves queue handoff and a legacy completion never gains proof", () => {
    const run = AssistantRunSchema.parse({ id: "callback", type: "schedule_callback", triggered_by: "voice", status: "completed",
        created_at: "2026-10-05T00:00:00Z", confirmation_allowed: true, receipt: { provider_status: "queued", job_id: "same-job" } });
    assert.equal(actionOutcome(run), "Queued for calling");
    assert.equal(actionOutcome({ ...run, actionType: "book_meeting", confirmationAllowed: false }), "Completion recorded — outcome unverified");
    assert.equal(actionOutcome({ ...run, actionType: "send_email", confirmationAllowed: false }), "Completion recorded — delivery unverified");
});
