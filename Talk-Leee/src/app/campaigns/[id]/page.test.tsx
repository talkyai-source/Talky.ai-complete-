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

function mockRunningCampaign(statsOverride: Record<string, unknown>, status = "running") {
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign: { ...campaign, status } }));
    mock.method(dashboardApi, "getCampaignStats", async () => ({ ...stats, ...statsOverride }));
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [], total: 0 }));
}

test("a running campaign that is blocked says why it is not dialing, and when it will", async () => {
    mockRunningCampaign({
        blocking_reason: {
            code: "line_not_ready",
            message: "No phone line is ready to dial out. Connect or switch on a line in Settings → Telephony.",
            severity: "error",
            next_eligible_at: "2026-10-19T13:00:00Z",
        },
        activity: { state: "blocked", work_remaining: true, waiting_on_schedule: false, schedule_override_active: false, reason_code: "line_not_ready", next_eligible_at: "2026-10-19T13:00:00Z" },
    });
    render(<Page />);
    const banner = await screen.findByText(/Not dialing: No phone line is ready to dial out/, undefined, { timeout: SLOW });
    assert.equal(banner.closest("[role='status']")?.textContent?.includes("Next attempt:"), true);
});

test("a campaign waiting for its calling window is described as waiting, not as finished", async () => {
    mockRunningCampaign({
        blocking_reason: null,
        activity: { state: "waiting_for_calling_window", work_remaining: true, waiting_on_schedule: true, schedule_override_active: false, reason_code: null, next_eligible_at: null },
    });
    render(<Page />);
    await screen.findByText(/Not dialing: Waiting for the calling window to open/, undefined, { timeout: SLOW });
});

test("normal pacing between calls is not reported as a block", async () => {
    mockRunningCampaign({
        blocking_reason: { code: "call_gap", message: "The campaign spaces its calls.", severity: "info", stage: "pacing" },
        activity: { state: "blocked", work_remaining: true, waiting_on_schedule: false, schedule_override_active: false, reason_code: "call_gap", next_eligible_at: null },
    });
    render(<Page />);
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Stop/ })), { timeout: SLOW });
    assert.equal(screen.queryByText(/Not dialing/), null);
});

test("a stale block on a campaign that is not running shows no banner", async () => {
    mockRunningCampaign({
        blocking_reason: { code: "out_of_minutes", message: "Out of minutes.", severity: "error" },
        activity: { state: "paused", work_remaining: false, waiting_on_schedule: false, schedule_override_active: false, reason_code: "out_of_minutes", next_eligible_at: null },
    }, "paused");
    render(<Page />);
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Start/ })), { timeout: SLOW });
    assert.equal(screen.queryByText(/Not dialing/), null);
});

test("a just-resumed campaign does not show the stale 'not running' block", async () => {
    mockRunningCampaign({
        blocking_reason: { code: "campaign_not_running", message: "The campaign isn't running, so queued calls are skipped.", severity: "warning" },
        activity: { state: "blocked", work_remaining: true, waiting_on_schedule: false, schedule_override_active: false, reason_code: "campaign_not_running", next_eligible_at: null },
    });
    render(<Page />);
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Stop/ })), { timeout: SLOW });
    assert.equal(screen.queryByText(/Not dialing/), null);
});

test("a per-contact reason does not read as the whole campaign not dialing", async () => {
    mockRunningCampaign({
        blocking_reason: { code: "dnc", message: "This number is on a Do-Not-Call list and will not be dialled.", severity: "error" },
        activity: { state: "blocked", work_remaining: true, waiting_on_schedule: false, schedule_override_active: false, reason_code: "dnc", next_eligible_at: null },
    });
    render(<Page />);
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Stop/ })), { timeout: SLOW });
    assert.equal(screen.queryByText(/Not dialing/), null);
});
