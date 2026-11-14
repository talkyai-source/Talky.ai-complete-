/**
 * FX2b Task 2.1, 2.2 — the Inbound list uses the shared states with its own
 * texts: loading is a status, a failed read shows its message with "Retry"
 * (never "Try again", never a validation dump), both empties are the shared
 * EmptyState, and the account switch loads and fails in the shared states.
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

let permissions: Q = {};
let list: Q = {};
let controls: Q = {};
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.endsWith("hooks/useAuth")) return { useAuth: () => ({ user: { id: "user-a", role: "user" } }) };
    if (id.includes("lib/queries/inbound-queries")) {
        return {
            useEffectivePermissions: () => permissions,
            useInboundCampaigns: () => list,
            useTenantInboundControls: (enabled: boolean) => (enabled ? controls : { isLoading: false, isError: false }),
            useSetTenantInboundControls: idleMutation,
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
void nothing;

const allowed = (names: string[]): Q => ({ isSuccess: true, isLoading: false, isError: false, data: { permissions: names } });
afterEach(() => { cleanup(); permissions = {}; list = {}; controls = {}; });

test("list: the permission check loading is a status with its own text", () => {
    permissions = { isLoading: true };
    render(<Page />);
    assert.equal(status("Checking inbound permissions…"), true);
});

test("list: a failed permission check shows its sentence with Retry", () => {
    let reads = 0;
    permissions = { isError: true, refetch: () => { reads += 1; } };
    render(<Page />);
    assert.equal(shown("Permissions could not be verified"), true);
    assert.equal(screen.getByText("Inbound data and routing controls remain unavailable until server permissions can be confirmed.").getAttribute("role"), "alert");
    noOldRetry();
    fireEvent.click(retry());
    assert.equal(reads, 1);
});

test("list: loading is a status titled 'Loading inbound campaigns…'", () => {
    permissions = allowed(["inbound:read"]);
    list = { isLoading: true };
    render(<Page />);
    assert.equal(status("Loading inbound campaigns…"), true);
});

test("list: a failed read shows the server's message with Retry, which reads again", () => {
    let reads = 0;
    permissions = allowed(["inbound:read"]);
    list = { isError: true, error: new Error("Inbound list is unavailable."), refetch: () => { reads += 1; } };
    render(<Page />);
    assert.equal(screen.getByText("Inbound list is unavailable.").getAttribute("role"), "alert");
    assert.equal(shown("Inbound data is unavailable"), true);
    noOldRetry();
    fireEvent.click(retry());
    assert.equal(reads, 1);
});

test("list: an answer the app could not read shows 'Please retry.', never the validation dump", () => {
    permissions = allowed(["inbound:read"]);
    list = { isError: true, error: dump(), refetch: () => {} };
    render(<Page />);
    assert.equal(shown("Please retry."), true);
    assert.equal(shown(/invalid_type|expected array/i), false);
});

test("list: no campaigns shows 'No inbound campaigns yet' in the shared EmptyState with its create link", () => {
    permissions = allowed(["inbound:manage"]);
    list = { data: [] };
    render(<Page />);
    const title = screen.getByText("No inbound campaigns yet");
    assert.equal(title.closest(".content-card") === null, true, "the shared EmptyState, not the page's own card");
    assert.equal(shown("Create a durable draft, assign a verified number, and let server readiness confirm the route before activation."), true);
    assert.equal(screen.getByRole("link", { name: "Create the first draft" }).getAttribute("href"), "/inbound-campaigns/new");
});

test("list: no match shows 'No campaigns match' in the shared EmptyState and keeps 'Clear filters'", () => {
    permissions = allowed(["inbound:read"]);
    list = { data: [campaign({})] };
    render(<Page />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Search inbound campaigns" }), { target: { value: "zzz" } });
    const title = screen.getByText("No campaigns match");
    assert.equal(title.closest(".content-card") === null, true, "the shared EmptyState, not the page's own card");
    assert.equal(shown("Clear the search or choose a different status."), true);
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    assert.equal(shown("No campaigns match"), false);
    assert.equal(shown("Front desk"), true);
});

test("account switch: loading is a status with its own text", () => {
    permissions = allowed(["inbound:read", "inbound:controls"]);
    list = { data: [] };
    controls = { isLoading: true };
    render(<Page />);
    assert.equal(status("Loading the authoritative switch…"), true);
});

test("account switch: 'could not be verified' is in the shared ErrorState with Retry, which reads again", () => {
    let reads = 0;
    permissions = allowed(["inbound:read", "inbound:controls"]);
    list = { data: [] };
    controls = { isError: true, refetch: () => { reads += 1; } };
    render(<Page />);
    const message = screen.getByText("The switch could not be verified, so it cannot be changed.");
    assert.equal(message.getAttribute("role"), "alert");
    assert.equal(shown("Something went wrong"), true, "the shared ErrorState");
    fireEvent.click(retry());
    assert.equal(reads, 1);
});
