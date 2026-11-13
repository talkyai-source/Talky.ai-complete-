/** FX1b Task 5 — the inbound list shows no false "Ready". */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";

import type { InboundCampaign } from "@/lib/inbound-api";

let campaigns: InboundCampaign[] = [];
let controls: { data?: { inbound_enabled: boolean; version: number; reason?: string | null }; isLoading: boolean; isError: boolean } = { isLoading: false, isError: false };
let permissionList: string[] = [];

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.endsWith("hooks/useAuth")) return { useAuth: () => ({ user: { id: "user-a", role: "user" } }) };
    if (id.includes("lib/queries/inbound-queries")) {
        return {
            useEffectivePermissions: () => ({ isSuccess: true, isLoading: false, isError: false, data: { permissions: permissionList } }),
            useInboundCampaigns: () => ({ data: campaigns, isLoading: false, isError: false }),
            useTenantInboundControls: (enabled: boolean) => (enabled ? controls : { isLoading: false, isError: false }),
            useSetTenantInboundControls: () => ({ isPending: false, mutateAsync: async () => {} }),
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

afterEach(() => {
    cleanup();
    campaigns = [];
    controls = { isLoading: false, isError: false };
    permissionList = [];
});

const READY = { ready: true, checks: [{ key: "did", label: "Number", passed: true, detail: "ok" }], blockers: [] };
const BLOCKED = { ready: false, checks: [], blockers: [{ code: "trunk_down", message: "Trunk is down", remediation: "Fix it" }] };

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

function card(name: string) {
    const article = screen.getByRole("heading", { name }).closest("article");
    assert.equal(article !== null, true);
    return within(article as HTMLElement);
}

test("with the inbound switch on, an active ready campaign shows Ready", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: true, version: 1 }, isLoading: false, isError: false };
    campaigns = [campaign({})];
    render(<Page />);
    assert.equal(card("Front desk").queryByText("Ready") !== null, true);
});

test("while the inbound switch is off, no card shows Ready", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: false, version: 1 }, isLoading: false, isError: false };
    campaigns = [campaign({}), campaign({ id: "inb-2", name: "Blocked line", status: "paused", readiness: BLOCKED })];
    render(<Page />);
    assert.equal(screen.queryByText("Disabled. New inbound calls fail closed before answer.") !== null, true);
    assert.equal(screen.queryByText("Ready") !== null, false);
    // The lifecycle status and a blocker count are still the truth and stay.
    assert.equal(card("Front desk").queryByText("Active") !== null, true);
    assert.equal(card("Blocked line").queryByText("1 blocker") !== null, true);
});

test("an archived campaign's card does not show Ready", () => {
    permissionList = ["inbound:read", "inbound:controls"];
    controls = { data: { inbound_enabled: true, version: 1 }, isLoading: false, isError: false };
    campaigns = [campaign({ status: "archived", name: "Old line" })];
    render(<Page />);
    assert.equal(card("Old line").queryByText("Archived") !== null, true);
    assert.equal(screen.queryByText("Ready") !== null, false);
});
