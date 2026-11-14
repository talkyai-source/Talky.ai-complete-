/**
 * 2.4 — EP4, the campaign detail "Add Contact" / edit form. AC-PH: with
 * United Kingdom selected 07400 123456 is sent as +447400123456; 12345 is
 * rejected with an inline error, Save is blocked and nothing is sent.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";
import { INVALID_PHONE_ERROR, PHONE_HELP_TEXT, SELECT_COUNTRY_ERROR } from "@/lib/phone-number";
import { BAD_NUMBER, UK_E164, UK_NATIONAL, chooseUnitedKingdom } from "@/test-utils/phone-fixtures";

// Same boundary stubs as page.test.tsx: layout, routing and the heavy child
// panels; the page, its form and the shared phone module run for real.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return {
        useParams: () => ({ id: "camp-1" }), useRouter: () => router,
        useSearchParams: () => new URLSearchParams(),
    };
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
afterEach(() => { cleanup(); mock.restoreAll(); });

const campaign = {
    id: "camp-1", name: "Spring push", status: "draft", direction: "outbound" as const,
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
};
const stats = { total_leads: 0, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} };

function mockPage() {
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign }));
    mock.method(dashboardApi, "getCampaignStats", async () => stats);
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    return mock.method(dashboardApi, "addContact", async () => ({ message: "ok", contact: { id: "c-1", phone_number: UK_E164 } }));
}

async function openForm() {
    render(<Page />);
    fireEvent.click(await screen.findByRole("button", { name: /Add contact/ }, { timeout: SLOW }));
    return await screen.findByLabelText("Phone Number", undefined, { timeout: SLOW }) as HTMLInputElement;
}

test("EP4: UK selected shows Saves as +447400123456 and sends exactly that; 12345 shows the inline error, blocks Save and sends nothing", async () => {
    const addContact = mockPage();
    const phone = await openForm();
    assert.equal(document.getElementById("phone-help")?.textContent, PHONE_HELP_TEXT);

    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: BAD_NUMBER } });
    await waitFor(() => assert.equal(document.getElementById("phone-status")?.textContent, INVALID_PHONE_ERROR), { timeout: SLOW });
    const save = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    assert.equal(save.disabled, true);
    fireEvent.submit(save.closest("form")!);
    assert.equal(addContact.mock.callCount(), 0);

    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
    assert.equal(save.disabled, false);
    fireEvent.click(save);
    await waitFor(() => assert.equal(addContact.mock.callCount(), 1), { timeout: SLOW });
    const [campaignId, payload] = addContact.mock.calls[0].arguments as [string, { phone_number: string }];
    assert.equal(campaignId, "camp-1");
    assert.equal(payload.phone_number, UK_E164);
});

test("EP4 with no country selected: a national number is the select-country error and Save stays blocked", async () => {
    const addContact = mockPage();
    const phone = await openForm();
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("phone-status")?.textContent, SELECT_COUNTRY_ERROR), { timeout: SLOW });
    assert.equal((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled, true);
    fireEvent.submit(phone.closest("form")!);
    assert.equal(addContact.mock.callCount(), 0);
});

// 2.4 F2 Task 1 — an optional number field follows the same rule as the main number.
test("EP4: an invalid mobile number shows its inline error, blocks Save and sends nothing; a valid one is sent as E.164", async () => {
    const addContact = mockPage();
    const phone = await openForm();
    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
    // Add the Mobile number detail through the "Add a contact detail" select.
    fireEvent.click(screen.getByRole("combobox", { name: "Add a contact detail" }));
    fireEvent.click(await screen.findByRole("option", { name: "Mobile number" }));
    const mobile = await screen.findByLabelText("Mobile number", undefined, { timeout: SLOW });
    fireEvent.change(mobile, { target: { value: BAD_NUMBER } });
    await waitFor(() => assert.equal(document.getElementById("contact-mobile_number-status")?.textContent, INVALID_PHONE_ERROR), { timeout: SLOW });
    const save = screen.getByRole("button", { name: "Add" }) as HTMLButtonElement;
    assert.equal(save.disabled, true, "Save is blocked by the invalid mobile number");
    fireEvent.submit(save.closest("form")!);
    assert.equal(addContact.mock.callCount(), 0);
    fireEvent.change(mobile, { target: { value: "07400 123457" } });
    await waitFor(() => assert.equal(document.getElementById("contact-mobile_number-status")?.textContent, "Saves as +447400123457"), { timeout: SLOW });
    assert.equal(save.disabled, false);
    fireEvent.click(save);
    await waitFor(() => assert.equal(addContact.mock.callCount(), 1), { timeout: SLOW });
    const payload = addContact.mock.calls[0].arguments[1] as { phone_number: string; mobile_number: string };
    assert.equal(payload.phone_number, UK_E164);
    assert.equal(payload.mobile_number, "+447400123457");
});
