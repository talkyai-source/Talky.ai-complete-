/** FX3a2 Task 3.4 — the campaign edit page gives the layout the campaign's name for its breadcrumb. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

// Same boundary stubs as page.states.test.tsx, except that the layout shows the breadcrumb names it was given.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "camp-1" }), useRouter: () => router };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children, breadcrumbLabels }: { children: React.ReactNode; breadcrumbLabels?: Record<string, string> }) => (
            <><output data-testid="breadcrumb-labels">{JSON.stringify(breadcrumbLabels ?? null)}</output>{children}</>
        ),
    };
    if (id.includes("components/campaigns/campaign-form")) return {
        CampaignForm: ({ initialData }: { initialData?: { name?: string } }) => <div data-testid="edit-form">{initialData?.name}</div>,
    };
    if (id.includes("components/campaigns/campaign-basics-editor")) return { CampaignBasicsEditor: () => <div data-testid="edit-form">Spring push</div> };
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
    script_config: { persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"] },
};
type Answer = Awaited<ReturnType<typeof dashboardApi.getCampaign>>;

test("FX3a2: the edit page names its own id crumb with the campaign's name once it is read", async () => {
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign } as unknown as Answer));
    render(<Page />);
    await waitFor(() => assert.equal(screen.getByTestId("breadcrumb-labels").textContent, JSON.stringify({ "camp-1": "Spring push" })), { timeout: SLOW });
});
