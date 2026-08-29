import assert from "node:assert/strict";
import { test } from "node:test";

import { getReviewCapabilities, isRetryableSubmitStatus } from "@/lib/review-permissions";

// The regression these guard: the backend gates GET /calls/{id}/review on
// calls:read and PUT /calls/{id}/review on calls:create, so the two must not be
// collapsed into one capability.
test("calls:read alone can read reviews but not write one", () => {
    assert.deepEqual(getReviewCapabilities(["calls:read"]), {
        canRead: true,
        canWrite: false,
        source: "effective_permissions",
    });
});

test("the readonly role's real permission set cannot write a review", () => {
    // Mirrors ROLE_DEFAULT_PERMISSIONS[UserRole.READONLY] in
    // backend/app/core/security/rbac.py — the account this bug stranded.
    const readonly = getReviewCapabilities([
        "campaigns:read",
        "inbound:read",
        "calls:read",
        "recordings:read",
        "analytics:read",
        "analytics:export",
        "tenants:read",
        "connectors:read",
    ]);
    assert.equal(readonly.canRead, true);
    assert.equal(readonly.canWrite, false);
});

test("calls:create grants write", () => {
    assert.deepEqual(getReviewCapabilities(["calls:read", "calls:create"]), {
        canRead: true,
        canWrite: true,
        source: "effective_permissions",
    });
});

test("platform:admin grants both", () => {
    assert.deepEqual(getReviewCapabilities(["platform:admin"]), {
        canRead: true,
        canWrite: true,
        source: "effective_permissions",
    });
});

test("permissions are matched case- and whitespace-insensitively", () => {
    assert.equal(getReviewCapabilities([" Calls:Create "]).canWrite, true);
});

test("a missing permission set fails closed and says so", () => {
    assert.deepEqual(getReviewCapabilities(), {
        canRead: false,
        canWrite: false,
        source: "unavailable",
    });
});

// The other half of the bug: a Try-again button that re-fires a request the
// server has already refused on authorization grounds.
test("authorization and validation refusals are not retryable", () => {
    assert.equal(isRetryableSubmitStatus(403), false);
    assert.equal(isRetryableSubmitStatus(401), false);
    assert.equal(isRetryableSubmitStatus(422), false);
    assert.equal(isRetryableSubmitStatus(404), false);
});

test("transport, rate-limit and server faults are retryable", () => {
    assert.equal(isRetryableSubmitStatus(undefined), true);
    assert.equal(isRetryableSubmitStatus(408), true);
    assert.equal(isRetryableSubmitStatus(429), true);
    assert.equal(isRetryableSubmitStatus(500), true);
    assert.equal(isRetryableSubmitStatus(503), true);
});
