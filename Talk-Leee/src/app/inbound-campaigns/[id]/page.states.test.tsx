/**
 * FX2b Task 2.1, 2.3 — the Inbound detail page uses the shared states: page
 * loading is a status, a failed campaign read and a failed readiness read
 * show their own texts with "Retry".
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { z } from "zod";

import type { InboundCampaign } from "@/lib/inbound-api";

type Q = { data?: unknown; isLoading?: boolean; isError?: boolean; isSuccess?: boolean; isFetching?: boolean; error?: unknown; refetch?: () => void };
const READY = { ready: true, checks: [{ key: "did", label: "Number", passed: true, detail: "ok" }], blockers: [] };
function campaign(overrides: Partial<InboundCampaign>): InboundCampaign {
    return {
        id: "inb-1", name: "Front desk", direction: "inbound", status: "paused", version: 1, config_version: 3, config_checksum: "x",
        campaign_id: "camp-1", sip_trunk_id: "trunk-1", opening_mode: "agent_first", greeting: "Hello", silence_timeout_seconds: 10,
        timezone: "Europe/London", weekly_schedule: [], holiday_policy: "closed", after_hours_action: "hangup", transfer_enabled: false,
        transfer_destinations: [], transfer_failure_action: "hangup", max_transfer_attempts: 1, max_transfer_hops: 1,
        max_call_duration_seconds: 600, recording_enabled: false, readiness: READY,
        created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
        ...overrides,
    } as InboundCampaign;
}
const dump = () => {
    const parsed = z.object({ items: z.array(z.string()) }).safeParse({ items: 1 });
    if (parsed.success) throw new Error("expected a validation failure");
    return parsed.error;
};
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
// The shared LoadingState: a status whose title is a heading.
const status = (title: string) => screen.queryAllByRole("status").some((node) => within(node).queryByRole("heading", { name: title }) !== null);
const retry = () => screen.getByRole("button", { name: "Retry" });
const noOldRetry = () => assert.equal(screen.queryByRole("button", { name: "Try again" }) === null, true, "no 'Try again'");

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

test("detail: loading is a status titled 'Loading inbound campaign…'", () => {
    campaignQuery = { isLoading: true };
    render(<Page />);
    assert.equal(status("Loading inbound campaign…"), true);
});

test("detail: a failed read shows the server's message with Retry, which reads again", () => {
    let reads = 0;
    campaignQuery = { isError: true, error: new Error("Campaign store is down."), refetch: () => { reads += 1; } };
    render(<Page />);
    assert.equal(shown("Campaign is unavailable"), true);
    assert.equal(screen.getByText("Campaign store is down.").getAttribute("role"), "alert");
    noOldRetry();
    fireEvent.click(retry());
    assert.equal(reads, 1);
});

test("detail: an answer the app could not read shows 'Please retry.', never the validation dump", () => {
    campaignQuery = { isError: true, error: dump(), refetch: () => {} };
    render(<Page />);
    assert.equal(shown("Please retry."), true);
    assert.equal(shown(/invalid_type|expected array/i), false);
});

test("detail: the readiness panel loading is a status with its own text", () => {
    campaignQuery = { data: campaign({ readiness: undefined }) };
    readinessQuery = { isFetching: true };
    render(<Page />);
    assert.equal(status("Checking activation readiness…"), true);
});

test("detail: a failed readiness read is a failed read for that panel only, with Retry", () => {
    let reads = 0;
    campaignQuery = { data: campaign({}) };
    readinessQuery = { isError: true, error: new Error(), refetch: () => { reads += 1; } };
    render(<Page />);
    assert.equal(shown("Readiness could not be refreshed"), true);
    assert.equal(screen.getByText("Try again.").getAttribute("role"), "alert");
    assert.equal(shown("Front desk"), true, "the rest of the page still shows");
    noOldRetry();
    fireEvent.click(retry());
    assert.equal(reads, 1);
});
