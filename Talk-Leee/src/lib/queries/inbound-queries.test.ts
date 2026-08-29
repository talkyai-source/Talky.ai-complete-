import assert from "node:assert/strict";
import { test } from "node:test";

import { QueryClient } from "@tanstack/react-query";

import type { InboundCampaign, InboundRuntimeCapabilities } from "@/lib/inbound-api";
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
            allowed_tools: [],
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
