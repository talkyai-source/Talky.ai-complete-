/**
 * FX2b Task 2.1 — the Inbound edit page uses the shared states: loading is a
 * status, a failed read shows its message with "Retry"; the "Deactivate
 * before editing" notice is not a failed read and offers no retry.
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
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "inb-1" }), useRouter: () => ({ push() {} }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/inbound/inbound-campaign-form")) return { InboundCampaignForm: () => <div data-testid="inbound-form" /> };
    if (id.endsWith("hooks/useAuth")) return { useAuth: () => ({ user: { id: "user-a", role: "user" } }) };
    if (id.includes("lib/queries/inbound-queries")) {
        return {
            useEffectivePermissions: () => ({ isSuccess: true, isLoading: false, isError: false, data: { permissions: ["inbound:manage"] } }),
            useInboundCampaign: () => campaignQuery,
            useUpdateInboundCampaign: idleMutation,
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
afterEach(() => { cleanup(); campaignQuery = {}; });

test("edit: loading is a status titled 'Loading inbound campaign…'", () => {
    campaignQuery = { isLoading: true };
    render(<Page />);
    assert.equal(status("Loading inbound campaign…"), true);
});

test("edit: a failed read shows the server's message with Retry, which reads again", () => {
    let reads = 0;
    campaignQuery = { isError: true, error: new Error("Campaign store is down."), refetch: () => { reads += 1; } };
    render(<Page />);
    assert.equal(shown("Campaign is unavailable"), true);
    assert.equal(screen.getByText("Campaign store is down.").getAttribute("role"), "alert");
    noOldRetry();
    fireEvent.click(retry());
    assert.equal(reads, 1);
});

test("edit: an answer the app could not read shows 'Please retry.', never the validation dump", () => {
    campaignQuery = { isError: true, error: dump(), refetch: () => {} };
    render(<Page />);
    assert.equal(shown("Please retry."), true);
    assert.equal(shown(/invalid_type|expected array/i), false);
});

test("edit: 'Deactivate before editing' is a notice, not a failed read: no Retry", () => {
    campaignQuery = { data: campaign({ status: "active" }) };
    render(<Page />);
    assert.equal(shown("Deactivate before editing"), true);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true);
    assert.equal(shown("Try these quick checks"), false);
});

test("edit: a loaded inactive campaign still shows the form", () => {
    campaignQuery = { data: campaign({}) };
    render(<Page />);
    assert.equal(screen.queryByTestId("inbound-form") !== null, true);
});
