import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { dashboardApi } from "@/lib/dashboard-api";

// Real page and real dashboardApi object (methods are mocked per test). Only
// layout, routing and the heavy child panels (which fetch on their own) are
// stubbed so the test exercises the page's own load/error state.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
// Stable identity like the real router: loadData depends on it.
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

// Generous: the full suite runs many files in parallel on a loaded machine.
const SLOW = 15_000;

afterEach(() => { cleanup(); mock.restoreAll(); });

const campaign = {
    id: "camp-1", name: "Spring push", status: "running", direction: "outbound" as const,
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
};
const stats = { total_leads: 7, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} };

test("a failed reload after Pause keeps the campaign controls and clears once a reload succeeds", async () => {
    let contactsFail = false;
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign }));
    mock.method(dashboardApi, "getCampaignStats", async () => stats);
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => {
        if (contactsFail) throw new Error("listContacts 503");
        return { items: [], total: 0 };
    });
    mock.method(dashboardApi, "stopCampaign", async () => ({ message: "ok", termination_summary: { status: "confirmed" } }));
    render(<Page />);
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Stop/ })), { timeout: SLOW });

    contactsFail = true;
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
    await waitFor(() => assert.ok(screen.getByText(/listContacts 503/)), { timeout: SLOW });
    // The campaign is still live: its controls and stats must stay on screen.
    assert.ok(screen.getByRole("button", { name: /Stop/ }), "Stop control must survive a failed reload");
    assert.ok(screen.getByRole("button", { name: /Pause/ }), "Pause control must survive a failed reload");
    assert.ok(screen.getByText("Spring push"));

    contactsFail = false;
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    await waitFor(() => assert.ok(screen.queryByText(/listContacts 503/) === null, "error banner should clear after a successful reload"), { timeout: SLOW });
    assert.ok(screen.getByRole("button", { name: /Stop/ }));
});

test("a failed first load still shows the error banner (nothing to keep on screen)", async () => {
    mock.method(dashboardApi, "getCampaign", async () => { throw new Error("getCampaign 500"); });
    render(<Page />);
    await waitFor(() => assert.ok(screen.getByText(/getCampaign 500/)), { timeout: SLOW });
    assert.ok(screen.queryByRole("button", { name: /Stop/ }) === null);
});
