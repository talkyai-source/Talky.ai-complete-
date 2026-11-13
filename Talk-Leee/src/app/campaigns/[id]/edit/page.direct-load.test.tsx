/** FX1a Task 1 — a direct load or refresh of /campaigns/{id}/edit shows its data without any click. */
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { screen } from "@testing-library/react";
import { expectDirectLoadShowsData, restoreDirectLoad } from "@/test-utils/direct-load";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "camp-1" }), useRouter: () => router };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    // The form itself has its own tests; here it only proves the page handed it the loaded campaign.
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

afterEach(restoreDirectLoad);

const campaign = {
    id: "camp-1", name: "Spring push", status: "draft", direction: "outbound",
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
    script_config: { persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"] },
};

test("direct load of /campaigns/{id}/edit waits for the sign-in check and then shows the campaign form", async () => {
    await expectDirectLoadShowsData(<Page />, [
        [/\/campaigns\/camp-1$/, { campaign }],
    ], () => screen.queryAllByTestId("edit-form").some((node) => node.textContent === "Spring push"));
});
