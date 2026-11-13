/** FX2a Task 1.3 — campaign edit: the shared loading state, and a failed read shows the shared error state with Retry. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    // Their side reads effective permissions (QA F1-21); unknown offers every action.
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: false, data: undefined }) };
    if (id === "next/navigation") return { useParams: () => ({ id: "camp-1" }), useRouter: () => router };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
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
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const formShown = () => screen.queryAllByTestId("edit-form").some((node) => node.textContent === "Spring push");

const campaign = {
    id: "camp-1", name: "Spring push", status: "draft", direction: "outbound" as const,
    system_prompt: "", voice_id: "v", max_concurrent_calls: 1, total_leads: 0,
    calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
    script_config: { persona_type: "lead_gen", company_name: "Acme", agent_names: ["Alex"] },
};
type Answer = Awaited<ReturnType<typeof dashboardApi.getCampaign>>;

test("the read is announced through the shared loading state", async () => {
    let answer!: (value: Answer) => void;
    mock.method(dashboardApi, "getCampaign", () => new Promise<Answer>((resolve) => { answer = resolve; }));
    render(<Page />);
    assert.equal(within(await screen.findByRole("status")).queryByText("Loading") !== null, true, "the shared LoadingState");
    await act(async () => { answer({ campaign } as unknown as Answer); });
    await waitFor(() => assert.equal(formShown(), true), { timeout: SLOW });
    assert.equal(screen.queryByRole("status") === null, true);
});

test("a failed read shows the server's message in the shared error state, and Retry reads the campaign again", async () => {
    let fail = true;
    const read = mock.method(dashboardApi, "getCampaign", async () => {
        if (fail) throw new Error("Campaign not found");
        return { campaign } as unknown as Answer;
    });
    render(<Page />);
    await waitFor(() => assert.equal(shown("Campaign not found"), true), { timeout: SLOW });
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    assert.equal(screen.getByText("Campaign not found").getAttribute("role"), "alert");
    assert.equal(formShown(), false);

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => assert.equal(formShown(), true), { timeout: SLOW });
    assert.equal(read.mock.callCount(), 2, "Retry sent the request again");
    assert.equal(shown("Campaign not found"), false);
});
