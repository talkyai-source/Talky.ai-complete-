import assert from "node:assert/strict";
import { test } from "node:test";

import { QueryClient } from "@tanstack/react-query";

import { inboundErrorCode, inboundErrorStatus, type InboundCampaign, type InboundRuntimeCapabilities } from "@/lib/inbound-api";
import { inboundStateForError } from "@/lib/inbound/inbound-types";
import { commitInboundCampaignCache, inboundQueryKeys } from "@/lib/queries/inbound-queries";

test("campaign cache commits never rewrite capability objects", () => {
    const client = new QueryClient();
    try {
        const capabilities: InboundRuntimeCapabilities = {
            transfer_runtime_available: true,
            transfer_platform_enabled: true,
            transfer_configuration_available: true,
        };
        const campaign: InboundCampaign = {
            id: "inbound-1",
            name: "Main line",
            direction: "inbound",
            status: "draft",
            version: 1,
            config_version: 1,
            config_checksum: "checksum",
            campaign_id: "campaign-1",
            sip_trunk_id: "trunk-1",
            opening_mode: "caller_first",
            greeting: "Hello",
            silence_timeout_seconds: 5,
            timezone: "UTC",
            weekly_schedule: [],
            holiday_policy: "closed",
            after_hours_action: "hangup",
            transfer_enabled: false,
            transfer_destinations: [],
            transfer_failure_action: "hangup",
            max_transfer_attempts: 1,
            max_transfer_hops: 1,
            max_call_duration_seconds: 1800,
            recording_enabled: false,
            readiness: { ready: false, checks: [], blockers: [] },
            created_at: "2026-08-28T00:00:00Z",
            updated_at: "2026-08-28T00:00:00Z",
        };
        const capabilityKey = inboundQueryKeys.capabilities(campaign.id);
        client.setQueryData(capabilityKey, capabilities);
        client.setQueryData(inboundQueryKeys.list(false), [] as InboundCampaign[]);

        commitInboundCampaignCache(client, campaign);

        assert.deepEqual(client.getQueryData(capabilityKey), capabilities);
        assert.deepEqual(client.getQueryData(inboundQueryKeys.list(false)), [campaign]);
    } finally {
        client.clear();
    }
});

/**
 * Regression guard for the state the old three-bucket mapper could not
 * express. A 503 `authorization_unavailable` means the permission LOOKUP
 * failed, not that the user was refused: rendering it as no-permission
 * tells them something false about their own access.
 */
test("a 503 authorization_unavailable is not rendered as no-permission", () => {
    const state = inboundStateForError(
        inboundErrorStatus({ status: 503, code: "authorization_unavailable" }),
        inboundErrorCode({ status: 503, code: "authorization_unavailable" }),
    );

    assert.equal(state, "authorization-unavailable");
    assert.notEqual(state, "no-permission");

    // A real 403 must still resolve to a denial, so the guard above is not
    // simply collapsing every permission failure into the softer state.
    assert.equal(
        inboundStateForError(
            inboundErrorStatus({ status: 403, code: "permission_denied" }),
            inboundErrorCode({ status: 403, code: "permission_denied" }),
        ),
        "no-permission",
    );

    // A bare 503 with no code carries the same meaning: status decides only
    // where the body named nothing.
    assert.equal(
        inboundStateForError(inboundErrorStatus({ status: 503 }), inboundErrorCode({ status: 503 })),
        "authorization-unavailable",
    );
});
