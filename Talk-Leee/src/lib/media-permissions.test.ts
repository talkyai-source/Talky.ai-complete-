import assert from "node:assert/strict";
import { test } from "node:test";

import { getRecordingCapabilities } from "@/lib/media-permissions";

test("recording permissions stay independent", () => {
    assert.deepEqual(getRecordingCapabilities(["recordings:read"]), {
        canRead: true,
        canDownload: false,
        canDelete: false,
        source: "effective_permissions",
    });
    assert.deepEqual(getRecordingCapabilities(["recordings:download"]), {
        canRead: false,
        canDownload: true,
        canDelete: false,
        source: "effective_permissions",
    });
    assert.deepEqual(getRecordingCapabilities(["recordings:delete"]), {
        canRead: false,
        canDownload: false,
        canDelete: true,
        source: "effective_permissions",
    });
});

test("broader call permissions do not grant recording access", () => {
    const capabilities = getRecordingCapabilities([
        "calls:read",
        "calls:delete",
        "calls:export",
    ]);
    assert.equal(capabilities.canRead, false);
    assert.equal(capabilities.canDownload, false);
    assert.equal(capabilities.canDelete, false);
});

test("platform admin is an explicit bypass and missing discovery fails closed", () => {
    const platformAdmin = getRecordingCapabilities(["platform:admin"]);
    assert.equal(platformAdmin.canRead, true);
    assert.equal(platformAdmin.canDownload, true);
    assert.equal(platformAdmin.canDelete, true);

    assert.deepEqual(getRecordingCapabilities(), {
        canRead: false,
        canDownload: false,
        canDelete: false,
        source: "unavailable",
    });
});
