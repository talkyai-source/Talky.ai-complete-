import assert from "node:assert/strict";
import { test } from "node:test";

import { getInboundCapabilities } from "@/lib/inbound-permissions";

test("effective permissions keep inbound capabilities distinct", () => {
    const viewer = getInboundCapabilities("tenant_admin", ["inbound:read", "recordings:read"]);
    assert.equal(viewer.canView, true);
    assert.equal(viewer.canCreate, false);
    assert.equal(viewer.canAssignNumber, false);
    assert.equal(viewer.canChangeLifecycle, false);
    assert.equal(viewer.canChangeControls, false);
    assert.equal(viewer.canPlayMedia, true);
    assert.equal(viewer.canDownloadMedia, false);

    const campaignOnlyManager = getInboundCapabilities("user", ["campaigns:admin", "recordings:read", "recordings:delete"]);
    assert.equal(campaignOnlyManager.canView, false);
    assert.equal(campaignOnlyManager.canCreate, false);
    assert.equal(campaignOnlyManager.canAssignNumber, false);
    assert.equal(campaignOnlyManager.canChangeLifecycle, false);
    assert.equal(campaignOnlyManager.canDeleteMedia, true);
    assert.equal(campaignOnlyManager.source, "effective_permissions");

    const inboundManager = getInboundCapabilities("user", ["inbound:read", "inbound:manage", "inbound:assign"]);
    assert.equal(inboundManager.canView, true);
    assert.equal(inboundManager.canCreate, true);
    assert.equal(inboundManager.canEdit, true);
    assert.equal(inboundManager.canAssignNumber, true);
    assert.equal(inboundManager.canChangeLifecycle, true);
    assert.equal(inboundManager.canChangeControls, false);

    const controller = getInboundCapabilities("user", ["inbound:read", "inbound:controls"]);
    assert.equal(controller.canChangeControls, true);
});

test("capabilities fail closed when server permission discovery is unavailable", () => {
    assert.equal(getInboundCapabilities("user").canCreate, false);
    assert.equal(getInboundCapabilities("partner_admin").canChangeLifecycle, false);
    assert.equal(getInboundCapabilities("tenant_admin").canChangeLifecycle, false);
    assert.equal(getInboundCapabilities("readonly").canView, false);
    assert.equal(getInboundCapabilities("platform_admin").source, "unavailable");
});
