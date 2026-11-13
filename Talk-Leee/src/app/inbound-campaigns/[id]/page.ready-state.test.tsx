/** FX1b Task 5 — the inbound detail page shows no false "Ready", and shows the failure text. */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import type { InboundCampaign, InboundReadiness } from "@/lib/inbound-api";

const READY: InboundReadiness = { ready: true, checks: [{ key: "did", label: "Number", passed: true, detail: "ok" }], blockers: [] } as InboundReadiness;
const TEST_HINT = "Test: call the number once active";

let current: InboundCampaign;
let controls: { data?: { inbound_enabled: boolean; version: number } } = {};
let permissionList: string[] = [];

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const idleMutation = () => ({ isPending: false, mutateAsync: async () => {} });
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
            useEffectivePermissions: () => ({ isSuccess: true, isLoading: false, isError: false, data: { permissions: permissionList } }),
            useInboundCampaign: () => ({ data: current, isLoading: false, isError: false }),
            useInboundReadiness: () => ({ data: current.readiness, isSuccess: true, isError: false, isFetching: false, refetch: () => {} }),
            useMinutesStatus: () => ({ data: undefined }),
            useTenantInboundControls: (enabled: boolean) => (enabled ? controls : {}),
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

afterEach(() => {
    cleanup();
    controls = {};
    permissionList = [];
});

test.after(() => {
    window.requestAnimationFrame = originalRequestAnimationFrame;
    window.cancelAnimationFrame = originalCancelAnimationFrame;
});

function campaign(overrides: Partial<InboundCampaign>): InboundCampaign {
    return {
        id: "inb-1", name: "Front desk", direction: "inbound", status: "active", version: 1, config_version: 3, config_checksum: "x",
        campaign_id: "camp-1", sip_trunk_id: "trunk-1", opening_mode: "agent_first", greeting: "Hello", silence_timeout_seconds: 10,
        timezone: "Europe/London", weekly_schedule: [], holiday_policy: "closed", after_hours_action: "hangup", transfer_enabled: false,
        transfer_destinations: [], transfer_failure_action: "hangup", max_transfer_attempts: 1, max_transfer_hops: 1,
        max_call_duration_seconds: 600, recording_enabled: false, readiness: READY,
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
        ...overrides,
    } as InboundCampaign;
}

test("an active ready campaign with the switch on shows Ready and the test hint", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: true, version: 1 } };
    current = campaign({});
    render(<Page />);
    assert.equal(screen.queryByText("Safe to request activation") !== null, true);
    assert.equal(screen.queryByText("Ready") !== null, true);
    assert.equal(screen.queryByText(TEST_HINT) !== null, true);
});

test("an archived campaign shows neither Ready nor the test hint", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: true, version: 1 } };
    current = campaign({ status: "archived" });
    render(<Page />);
    assert.equal(screen.queryByText("Archived") !== null, true);
    assert.equal(screen.queryByText("Ready") !== null, false);
    assert.equal(screen.queryByText(TEST_HINT) !== null, false);
});

test("while the inbound switch is off the detail page does not show Ready", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: false, version: 1 } };
    current = campaign({});
    render(<Page />);
    assert.equal(screen.queryByText("Active") !== null, true);
    assert.equal(screen.queryByText("Ready") !== null, false);
});

test("a paused campaign's failure text shows on its detail page", () => {
    permissionList = ["inbound:read"];
    current = campaign({ status: "paused", last_error: "Paused by the server: trunk registration failed" });
    render(<Page />);
    assert.equal(screen.queryByText("Paused by the server: trunk registration failed") !== null, true);
});

// ── FX1c Task 4 ─────────────────────────────────────────────────────────────

test("while the inbound switch is off the checklist does not show Safe to request activation", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: false, version: 1 } };
    current = campaign({});
    render(<Page />);
    assert.equal(screen.queryByText("Safe to request activation") !== null, false);
    assert.equal(screen.queryByText("The server has verified the current phone number, agent and telephony configuration.") !== null, false);
    // The checks themselves are still listed.
    assert.equal(screen.queryByText("Number") !== null, true);
});

test("with the switch off a blocked campaign still shows Activation is blocked", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: false, version: 1 } };
    current = campaign({ status: "paused", readiness: { ready: false, checks: [], blockers: [{ code: "trunk_down", message: "Trunk is down", remediation: "Fix it" }] } as InboundReadiness });
    render(<Page />);
    assert.equal(screen.queryByText("Activation is blocked") !== null, true);
});
