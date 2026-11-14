import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { dashboardApi } from "@/lib/dashboard-api";

// QA F1-21 (QA-UI-041): the campaign page offered a read-only role Edit,
// Start/Pause/Stop and every contact change (import, add, edit, delete,
// list switches and "Call this list"); the server refuses them all without
// campaigns:update. Same harness as page.test.tsx, with the permissions set.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const router = { push() {}, replace(_href: string) {} };
let permissionsQuery: { isSuccess: boolean; data?: { permissions: string[]; role?: string } } = { isSuccess: false };
let contactListsReadOnly: boolean | undefined;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return {
        useParams: () => ({ id: "camp-1" }), useRouter: () => router, useSearchParams: () => new URLSearchParams(),
    };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/smart-csv-import")) return { SmartCsvImport: nothing };
    if (id.includes("components/campaigns/contact-lists")) return {
        ContactLists: ({ readOnly }: { readOnly?: boolean }) => { contactListsReadOnly = readOnly; return null; },
        ActiveContactsSummary: nothing,
    };
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
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissionsQuery };
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
afterEach(() => { cleanup(); mock.restoreAll(); permissionsQuery = { isSuccess: false }; contactListsReadOnly = undefined; });

const contact = {
    id: "lead-1", phone_number: "+442071234567", first_name: "Ada", last_name: "Lovelace", email: null,
    status: "pending", call_attempts: 0, last_call_result: "pending", created_at: "2026-11-01T00:00:00Z",
};
function load(status: string) {
    mock.method(dashboardApi, "getCampaign", async () => ({ campaign: {
        id: "camp-1", name: "Spring push", status, direction: "outbound" as const, system_prompt: "", voice_id: "v",
        max_concurrent_calls: 1, total_leads: 1, calls_completed: 0, calls_failed: 0, created_at: "2026-01-01T00:00:00Z",
    } }));
    mock.method(dashboardApi, "getCampaignStats", async () => ({ total_leads: 1, goals_achieved: 0, job_status_counts: {}, call_outcome_counts: {} }));
    mock.method(dashboardApi, "getMinutesStatus", async () => { throw new Error("quota"); });
    mock.method(dashboardApi, "listContacts", async () => ({ items: [contact], total: 1 }));
}
const absent = (name: RegExp) => screen.queryByRole("button", { name }) === null;

for (const status of ["paused", "running"]) {
    test(`a read-only role sees a ${status} campaign and its contacts without change controls`, async () => {
        permissionsQuery = { isSuccess: true, data: { permissions: ["campaigns:read", "calls:read"], role: "readonly" } };
        load(status);
        render(<Page />);
        await screen.findByText("+442071234567", undefined, { timeout: SLOW });
        // A paused campaign's start control reads Resume (plan 3.2); Duplicate needs campaigns:create (plan 3.1).
        for (const name of [/^Edit$/, /^Start$/, /^Resume$/, /^Pause$/, /^Stop$/, /^Duplicate$/, /Import CSV/, /Add contact/, /^Edit \+44/, /^Remove \+44/]) {
            assert.equal(absent(name), true, `${name} offered`);
        }
        assert.equal(contactListsReadOnly, true);
    });
}

test("a role that may change campaigns keeps every control", async () => {
    permissionsQuery = { isSuccess: true, data: { permissions: ["campaigns:read", "campaigns:update"], role: "user" } };
    load("paused");
    render(<Page />);
    await screen.findByText("+442071234567", undefined, { timeout: SLOW });
    // A paused campaign's start control reads Resume (plan 3.2), not Start.
    for (const name of [/^Edit$/, /^Resume$/, /Import CSV/, /Add contact/, /^Edit \+44/, /^Remove \+44/]) {
        assert.ok(screen.getByRole("button", { name }), `${name} missing`);
    }
    await waitFor(() => assert.equal(contactListsReadOnly, false));
});

// A duplicate is a new campaign: it needs campaigns:create, whatever the role may change (plan 3.1).
for (const [permission, offered] of [["campaigns:update", false], ["campaigns:create", true]] as const) {
    test(`Duplicate is ${offered ? "offered" : "not offered"} to a role with campaigns:read and ${permission}`, async () => {
        permissionsQuery = { isSuccess: true, data: { permissions: ["campaigns:read", permission], role: "user" } };
        load("paused");
        render(<Page />);
        await screen.findByText("+442071234567", undefined, { timeout: SLOW });
        assert.equal(absent(/^Duplicate$/), !offered);
    });
}
