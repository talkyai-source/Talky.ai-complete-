/** FX1a Task 1 — a direct load or refresh of /campaigns/{id} shows its data without any click. */
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { screen } from "@testing-library/react";
import { expectDirectLoadShowsData, restoreDirectLoad } from "@/test-utils/direct-load";

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

afterEach(restoreDirectLoad);

const campaign = {
    id: "camp-1", name: "Spring push", status: "running", direction: "outbound",
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
};

test("direct load of /campaigns/{id} waits for the sign-in check and then shows the campaign", async () => {
    await expectDirectLoadShowsData(<Page />, [
        [/\/campaigns\/camp-1\/contacts$/, { items: [], total: 0, page: 1, page_size: 100 }],
        [/\/campaigns\/camp-1\/stats$/, { campaign_id: "camp-1", campaign_status: "running", total_leads: 7, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }],
        [/\/campaigns\/camp-1$/, { campaign }],
    ], () => screen.queryByRole("button", { name: /Stop/ }) !== null);
});
