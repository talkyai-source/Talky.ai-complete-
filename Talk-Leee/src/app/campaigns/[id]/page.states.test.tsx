/**
 * FX2a Task 1.2, 2.2, 2.3 — campaign detail: the shared loading and error
 * states with Retry; contact add / edit / delete failures shown inline with
 * the text the browser alert used to show; Remove contact asked in the app's
 * confirm dialog. No alert() and no confirm().
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "camp-1" }), useRouter: () => router, useSearchParams: () => new URLSearchParams() };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/smart-csv-import")) return { SmartCsvImport: nothing };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing, ActiveContactsSummary: nothing };
    if (id.includes("components/campaigns/script-card")) return { ScriptCard: nothing };
    if (id.includes("components/campaigns/live-calls-panel")) return { LiveCallsPanel: nothing };
    if (id.includes("components/campaigns/call-issues-panel")) return { CallIssuesPanel: nothing };
    if (id.includes("components/campaigns/knowledge-panel")) return { KnowledgePanel: nothing };
    if (id.includes("components/campaigns/test-agent-button")) return { TestAgentButton: nothing };
    if (id.includes("components/campaigns/campaign-readiness")) return {
        CampaignReadinessNotice: nothing,
        useCampaignReadiness: () => ({ ready: true, checking: false, reason: "", error: false, retry: async () => [] }),
    };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
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
const PHONE = "+447400123456";
const CONFIRM_SENTENCE = `Remove ${PHONE} from this campaign? It will no longer be dialed.`;
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const originalAlert = window.alert;
const originalConfirm = window.confirm;
let popups = 0;

beforeEach(() => {
    popups = 0;
    window.alert = () => { popups += 1; };
    window.confirm = () => { popups += 1; return true; };
});
afterEach(() => {
    cleanup();
    mock.restoreAll();
    window.alert = originalAlert;
    window.confirm = originalConfirm;
});

const campaign = {
    id: "camp-1", name: "Spring push", status: "draft", direction: "outbound" as const,
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 1,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
};
const stats = { total_leads: 1, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} };
const contact = { id: "k-1", phone_number: PHONE, first_name: "Ada", last_name: "Byron", status: "pending", call_attempts: 0 };

function mockReads() {
    mock.method(dashboardApi, "getCampaignStats", async () => stats);
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [contact], total: 1 }));
}
function mockPage() {
    mockReads();
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign }));
}

test("a campaign with no contacts shows the shared empty state with the existing sentence", async () => {
    mock.method(dashboardApi, "getCampaignStats", async () => stats);
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign }));
    render(<Page />);
    const title = await screen.findByRole("heading", { name: "No contacts yet. Add contacts to start your campaign." }, { timeout: SLOW });
    assert.equal(title.parentElement!.querySelector("p") === null, true, "no empty description line");
});

test("the first load is announced through the shared loading state", async () => {
    mockReads();
    let answer!: (value: { campaign: typeof campaign }) => void;
    mock.method(dashboardApi, "getCampaign", () => new Promise<{ campaign: typeof campaign }>((resolve) => { answer = resolve; }));
    render(<Page />);
    const status = await screen.findByRole("status");
    assert.equal(within(status).queryByText("Loading") !== null, true, "the shared LoadingState");
    await act(async () => { answer({ campaign }); });
    await screen.findByRole("heading", { name: "Spring push" }, { timeout: SLOW });
    assert.equal(shown("Loading"), false);
});

test("a first-load failure shows the server's message in the shared error state, and Retry reads the campaign again", async () => {
    mockReads();
    let fail = true;
    const read = mock.method(dashboardApi, "getCampaign", async () => {
        if (fail) throw new Error("Campaign not found");
        return { campaign };
    });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Campaign not found"), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(screen.getByText("Campaign not found").getAttribute("role"), "alert");

    fail = false;
    const before = read.mock.callCount();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("heading", { name: "Spring push" }, { timeout: SLOW });
    assert.equal(read.mock.callCount() > before, true, "Retry sent the request again");
    assert.equal(shown("Campaign not found"), false);
});

test("a failed add shows the server's message inline in the form, with no browser alert", async () => {
    mockPage();
    const add = mock.method(dashboardApi, "addContact", async () => { throw new Error("This number is on the do-not-call list"); });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW }));
    const phone = await screen.findByLabelText("Phone Number", undefined, { timeout: SLOW });
    fireEvent.change(phone, { target: { value: "+447400123457" } });
    const save = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    await waitFor(() => assert.equal(save.disabled, false), { timeout: SLOW });
    fireEvent.click(save);

    const form = within(phone.closest("form")!);
    await waitFor(() => assert.equal(form.queryByText("This number is on the do-not-call list") !== null, true), { timeout: SLOW });
    assert.equal(form.getByText("This number is on the do-not-call list").getAttribute("role"), "alert");
    assert.equal(add.mock.callCount(), 1);
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("a failed edit shows the server's message inline in the form, with no browser alert", async () => {
    mockPage();
    mock.method(dashboardApi, "updateContact", async () => { throw new Error("Contact was changed by someone else"); });
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: `Edit ${PHONE}` }, { timeout: SLOW }));
    const save = await screen.findByRole("button", { name: "Save changes" }, { timeout: SLOW }) as HTMLButtonElement;
    await waitFor(() => assert.equal(save.disabled, false), { timeout: SLOW });
    fireEvent.click(save);

    const form = within(save.closest("form")!);
    await waitFor(() => assert.equal(form.queryByText("Contact was changed by someone else") !== null, true), { timeout: SLOW });
    assert.equal(form.getByText("Contact was changed by someone else").getAttribute("role"), "alert");
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("Remove contact asks in the app's confirm dialog; Cancel sends nothing", async () => {
    mockPage();
    const remove = mock.method(dashboardApi, "deleteContact", async () => ({}));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: `Remove ${PHONE}` }, { timeout: SLOW }));

    const dialog = within(await screen.findByRole("dialog"));
    assert.equal(dialog.queryByText(CONFIRM_SENTENCE) !== null, true, "the existing confirm sentence is the body");
    assert.equal(dialog.queryAllByText("Remove contact").length, 2, "title and confirm button");
    fireEvent.click(dialog.getByRole("button", { name: "Cancel" }));
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(remove.mock.callCount(), 0);
    assert.equal(screen.queryByRole("button", { name: `Remove ${PHONE}` }) !== null, true, "the contact is still listed");
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("confirming Remove contact deletes the contact", async () => {
    mockPage();
    const remove = mock.method(dashboardApi, "deleteContact", async () => ({}));
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: `Remove ${PHONE}` }, { timeout: SLOW }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove contact" }));

    await waitFor(() => assert.equal(remove.mock.callCount(), 1), { timeout: SLOW });
    assert.deepEqual(remove.mock.calls[0]!.arguments, ["camp-1", "k-1"]);
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(popups, 0, "no alert() or confirm()");
});

test("a failed delete shows the server's message inline in the contact's row, with no browser alert", async () => {
    mockPage();
    mock.method(dashboardApi, "deleteContact", async () => { throw new Error("Contact is in an active call"); });
    render(<Page />);
    const trash = await screen.findByRole("button", { name: `Remove ${PHONE}` }, { timeout: SLOW });
    fireEvent.click(trash);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove contact" }));

    await waitFor(() => assert.equal(shown("Contact is in an active call"), true), { timeout: SLOW });
    const message = screen.getByText("Contact is in an active call");
    assert.equal(message.getAttribute("role"), "alert");
    assert.equal(message.closest("tr") === trash.closest("tr"), true, "shown in the row it came from");
    await waitFor(() => assert.equal(screen.queryByRole("dialog") === null, true), { timeout: SLOW });
    assert.equal(popups, 0, "no alert() or confirm()");
});
