/** FX3a2 Task 3.4 — the campaign detail page gives the layout the campaign's name for its breadcrumb. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

// Same boundary stubs as page.contacts.test.tsx, except that the layout shows the breadcrumb names it was given.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "camp-1" }), useRouter: () => router, useSearchParams: () => new URLSearchParams() };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children, breadcrumbLabels }: { children: React.ReactNode; breadcrumbLabels?: Record<string, string> }) => (
            <><output data-testid="breadcrumb-labels">{JSON.stringify(breadcrumbLabels ?? null)}</output>{children}</>
        ),
    };
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

test("FX3a2: the detail page names its own id crumb with the campaign's name", async () => {
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign }));
    mock.method(dashboardApi, "getCampaignStats", async () => ({ total_leads: 0, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }));
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
    render(<Page />);
    await waitFor(() => assert.equal(screen.getByTestId("breadcrumb-labels").textContent, JSON.stringify({ "camp-1": "Spring push" })), { timeout: SLOW });
});
