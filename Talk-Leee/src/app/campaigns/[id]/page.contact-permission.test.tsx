/**
 * Permission to call on the campaign detail "Add contact" form: nothing is sent
 * until the confirmation block is complete; then the request carries it, and a
 * backend refusal shows in the block.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";
import { ApiClientError } from "@/lib/http-client";
import { EXPECTED_PERMISSION, fillContactPermission } from "@/test-utils/contact-permission";
import { UK_E164, UK_NATIONAL, chooseUnitedKingdom } from "@/test-utils/phone-fixtures";

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

async function fillPhone(phone: HTMLInputElement) {
    await chooseUnitedKingdom("contact-country");
    fireEvent.change(phone, { target: { value: UK_NATIONAL } });
    await waitFor(() => assert.equal(document.getElementById("phone-status")?.textContent, `Saves as ${UK_E164}`), { timeout: SLOW });
}

test("add contact: empty block sends nothing and shows the errors; a complete block sends the contact and the permission object", async () => {
    const addContact = mockPage();
    await fillPhone(await openForm());
    const save = screen.getByRole("button", { name: "Add" });
    assert.equal(screen.queryByText("Tick this box to confirm."), null, "no error before the first attempt");
    fireEvent.click(save);
    assert.equal(addContact.mock.callCount(), 0);
    assert.ok(screen.getByText("Choose why you can call them."));
    assert.ok(screen.getByText("Tick this box to confirm."));

    fillContactPermission();
    fireEvent.click(save);
    await waitFor(() => assert.equal(addContact.mock.callCount(), 1), { timeout: SLOW });
    const [campaignId, payload, permission] = addContact.mock.calls[0].arguments as unknown as [string, { phone_number: string }, unknown];
    assert.equal(campaignId, "camp-1");
    assert.equal(payload.phone_number, UK_E164);
    assert.deepEqual(permission, EXPECTED_PERMISSION);
});

test("add contact: a 422 for the date shows beside the date field and no generic error", async () => {
    mockPage();
    mock.method(dashboardApi, "addContact", async () => {
        throw new ApiClientError({
            status: 422,
            code: "contact_permission_invalid",
            message: "The date cannot be in the future.",
            details: { code: "contact_permission_invalid", field: "permission_date", message: "The date cannot be in the future." },
            url: "u",
            method: "POST",
        });
    });
    await fillPhone(await openForm());
    fillContactPermission();
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    const date = screen.getByLabelText("Date permission was given");
    await waitFor(() => assert.equal(date.getAttribute("aria-invalid"), "true"), { timeout: SLOW });
    const described = (date.getAttribute("aria-describedby") ?? "").split(" ").map((id) => document.getElementById(id)?.textContent);
    assert.ok(described.includes("The date cannot be in the future."));
    assert.equal(screen.queryByText("Failed to add contact"), null);
});
