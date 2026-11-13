/**
 * FX3b2 Task 1.3 - the edit page notice says "phone number", not DID.
 * Same boundary stubs as page.states.test.tsx.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

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
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

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

test("FX3b2 1.3: the notice on an active campaign says 'phone number', not DID", () => {
    campaignQuery = { data: campaign({ status: "active" }) };
    render(<Page />);
    assert.equal(shown("Live phone number routing cannot be edited. Deactivate this campaign, then return to edit the configuration."), true);
    assert.equal(/\bDID\b/.test(document.body.textContent ?? ""), false);
});
