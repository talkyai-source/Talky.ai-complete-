/**
 * FX3b Task 3.2 — the Inbound list card names the trunk "SIP trunk", the name
 * the detail page and the form use. Same boundary stubs as page.states.test.tsx.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import type { InboundCampaign } from "@/lib/inbound-api";

type Q = { data?: unknown; isLoading?: boolean; isError?: boolean; isSuccess?: boolean; isFetching?: boolean; error?: unknown; refetch?: () => void };
const READY = { ready: true, checks: [{ key: "did", label: "Number", passed: true, detail: "ok" }], blockers: [] };
const campaign = {
    id: "inb-1", name: "Front desk", direction: "inbound", status: "paused", version: 1, config_version: 3, config_checksum: "x",
    campaign_id: "camp-1", sip_trunk_id: "trunk-1", sip_trunk_name: "Office line", opening_mode: "agent_first", greeting: "Hello", silence_timeout_seconds: 10,
    timezone: "Europe/London", weekly_schedule: [], holiday_policy: "closed", after_hours_action: "hangup", transfer_enabled: false,
    transfer_destinations: [], transfer_failure_action: "hangup", max_transfer_attempts: 1, max_transfer_hops: 1,
    max_call_duration_seconds: 600, recording_enabled: false, readiness: READY,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
} as unknown as InboundCampaign;

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const idleMutation = () => ({ isPending: false, mutateAsync: async () => {} });
let list: Q = {};
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.endsWith("hooks/useAuth")) return { useAuth: () => ({ user: { id: "user-a", role: "user" } }) };
    if (id.includes("lib/queries/inbound-queries")) {
        return {
            useEffectivePermissions: () => ({ isSuccess: true, isLoading: false, isError: false, data: { permissions: ["inbound:read"] } }),
            useInboundCampaigns: () => list,
            useTenantInboundControls: () => ({ isLoading: false, isError: false }),
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

afterEach(() => { cleanup(); list = {}; });

test("FX3b 3.2: the list card names the trunk 'SIP trunk'", () => {
    list = { isSuccess: true, isLoading: false, isError: false, data: [campaign] };
    render(<Page />);
    const terms = Array.from(document.querySelectorAll("dt")).map((node) => node.textContent);
    assert.equal(terms.includes("SIP trunk"), true, terms.join(" | "));
    assert.equal(terms.includes("Inbound trunk"), false);
    const value = Array.from(document.querySelectorAll("dt")).find((node) => node.textContent === "SIP trunk")?.nextElementSibling?.textContent;
    assert.equal(value, "Office line");
});

test("FX3b2 1.4: the search box names the trunk 'SIP trunk', as the card does", () => {
    list = { isSuccess: true, isLoading: false, isError: false, data: [campaign] };
    render(<Page />);
    const search = screen.getByRole("searchbox", { name: "Search inbound campaigns" });
    assert.equal(search.getAttribute("placeholder"), "Search campaign, number, or SIP trunk");
    assert.equal(screen.queryByPlaceholderText("Search campaign, number, or trunk") !== null, false);
});
