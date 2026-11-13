/**
 * FX3a Tasks 2 and 3 — text and labels on the campaign detail page: the
 * contacts count card, the Add contact button and a contact row's name,
 * result label and remove button.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

// Same boundary stubs as page.contacts.test.tsx.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "camp-1" }), useRouter: () => router };
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
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 1,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
};
const contact = {
    id: "lead-1", campaign_id: "camp-1", phone_number: "+447400123456", first_name: "", last_name: "",
    status: "pending", last_call_result: "no_answer", call_attempts: 1, is_lead: false,
};

async function mountPage() {
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign }));
    mock.method(dashboardApi, "getCampaignStats", async () => ({ total_leads: 1, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }));
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [contact], total: 1 }));
    render(<Page />);
    await waitFor(() => assert.equal(screen.queryByText("+447400123456") !== null, true), { timeout: SLOW });
}

test("FX3a: the count card is named Contacts and the button reads Add contact", async () => {
    await mountPage();
    assert.equal(screen.queryByText("Total Leads") !== null, false);
    const card = screen.getAllByText("Contacts").find((el) => el.tagName === "P");
    assert.equal(card !== undefined, true);
    assert.equal(card!.parentElement!.textContent, "1Contacts");
    assert.equal(screen.queryByRole("button", { name: "Add contact" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Add Contact" }) !== null, false);
});

test("FX3a: a contact row shows the result label, Name unavailable and a Remove button", async () => {
    await mountPage();
    const row = screen.getByText("+447400123456").closest("tr")!;
    const cells = Array.from(row.querySelectorAll("td")).map((cell) => cell.textContent ?? "");
    assert.equal(cells[1], "Name unavailable");
    assert.equal(cells[2], "No Answer");
    assert.equal((row.textContent ?? "").includes("no_answer"), false);
    assert.equal((row.textContent ?? "").includes("--"), false);
    assert.equal(screen.queryByRole("button", { name: "Remove +447400123456" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Delete +447400123456" }) !== null, false);
});
