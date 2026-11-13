/**
 * FX3b Task 2.7 and 3.5 — on the Inbound detail page the link to the calls
 * page uses that page's name, "Call History", and a readiness blocker prints
 * the app's label for its code, not the raw code.
 * Same boundary stubs as page.states.test.tsx.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import type { InboundCampaign } from "@/lib/inbound-api";
import { REASON_LABELS } from "@/lib/inbound-reason-labels";

type Q = { data?: unknown; isLoading?: boolean; isError?: boolean; isSuccess?: boolean; isFetching?: boolean; error?: unknown; refetch?: () => void };
const BLOCKED = {
    ready: false,
    checks: [{ key: "trunk", label: "Trunk", passed: false, detail: "not registered" }],
    blockers: [
        { code: "trunk_not_ready", message: "The trunk is not registered.", remediation: "Register the trunk." },
        { code: "code_added_later", message: "Something else is missing.", remediation: "Fix it." },
    ],
};
const campaign = {
    id: "inb-1", name: "Front desk", direction: "inbound", status: "paused", version: 1, config_version: 3, config_checksum: "x",
    campaign_id: "camp-1", sip_trunk_id: "trunk-1", opening_mode: "agent_first", greeting: "Hello", silence_timeout_seconds: 10,
    timezone: "Europe/London", weekly_schedule: [], holiday_policy: "closed", after_hours_action: "hangup", transfer_enabled: false,
    transfer_destinations: [], transfer_failure_action: "hangup", max_transfer_attempts: 1, max_transfer_hops: 1,
    max_call_duration_seconds: 600, recording_enabled: false, readiness: BLOCKED,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
} as unknown as InboundCampaign;

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const idleMutation = () => ({ isPending: false, mutateAsync: async () => {} });
let campaignQuery: Q = {};
let readinessQuery: Q = {};
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "inb-1" }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/call-issues-panel")) return { CallIssuesPanel: nothing };
    if (id.includes("components/campaigns/knowledge-panel")) return { KnowledgePanel: nothing };
    if (id.includes("components/campaigns/live-calls-panel")) return { LiveCallsPanel: nothing };
    if (id.includes("components/campaigns/rejected-inbound-calls-panel")) return { RejectedInboundCallsPanel: nothing };
    if (id.endsWith("hooks/useAuth")) return { useAuth: () => ({ user: { id: "user-a", role: "user" } }) };
    if (id.includes("lib/queries/inbound-queries")) {
        return {
            useEffectivePermissions: () => ({ isSuccess: true, isLoading: false, isError: false, data: { permissions: ["inbound:read"] } }),
            useInboundCampaign: () => campaignQuery,
            useInboundReadiness: () => readinessQuery,
            useMinutesStatus: () => ({ data: undefined }),
            useTenantInboundControls: () => ({}),
            useActivateInboundCampaign: idleMutation,
            usePauseInboundCampaign: idleMutation,
            useArchiveInboundCampaign: idleMutation,
        };
    }
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const originalRequestAnimationFrame = window.requestAnimationFrame;
const originalCancelAnimationFrame = window.cancelAnimationFrame;
window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
afterEach(() => { cleanup(); campaignQuery = {}; readinessQuery = {}; });
test.after(() => {
    window.requestAnimationFrame = originalRequestAnimationFrame;
    window.cancelAnimationFrame = originalCancelAnimationFrame;
});

function mount() {
    campaignQuery = { isSuccess: true, isLoading: false, isError: false, data: campaign };
    readinessQuery = { isSuccess: true, isLoading: false, isError: false, isFetching: false, data: BLOCKED, refetch: () => {} };
    render(<Page />);
}

test("FX3b 2.7: the link to the calls page is named 'Call History'", () => {
    mount();
    const link = screen.queryByRole("link", { name: "Call History" });
    assert.equal(link !== null, true);
    assert.equal(link!.getAttribute("href")?.startsWith("/calls?direction=inbound"), true);
    assert.equal(screen.queryByRole("link", { name: "Call history" }) !== null, false);
});

test("FX3b 3.5: a readiness blocker prints the app's label for its code; an unknown code stays as sent", () => {
    mount();
    assert.equal(REASON_LABELS.trunk_not_ready, "Phone trunk unavailable");
    assert.equal(screen.queryAllByText("The trunk is not registered.").length, 1);
    assert.equal(screen.queryAllByText("Phone trunk unavailable").length, 1);
    assert.equal(screen.queryAllByText("trunk_not_ready").length, 0);
    assert.equal(screen.queryAllByText("code_added_later").length, 1);
});

test("FX3b2 1.1: the test steps name the calls page 'Call History'", () => {
    mount();
    const text = document.body.textContent ?? "";
    assert.equal(text.includes("Confirm the call appears below and ends in Call History."), true);
    assert.equal(text.includes("ends in Call history."), false);
});

test("FX3b2 1.3: the summary of a ready campaign says 'phone number', not DID", () => {
    const ready = { ready: true, checks: [{ key: "did", label: "Number", passed: true, detail: "ok" }], blockers: [] };
    campaignQuery = { isSuccess: true, isLoading: false, isError: false, data: { ...campaign, readiness: ready } };
    readinessQuery = { isSuccess: true, isLoading: false, isError: false, isFetching: false, data: ready, refetch: () => {} };
    render(<Page />);
    assert.equal(screen.queryAllByText("The server has verified the current phone number, agent and telephony configuration.").length, 1);
    assert.equal(/\bDID\b/.test(document.body.textContent ?? ""), false);
});
