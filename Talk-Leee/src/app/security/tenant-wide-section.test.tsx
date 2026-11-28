import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";

// The Security page's "Tenant-wide security" cards open /admin and
// /admin/audit-logs. Those pages are for platform and partner administrators
// only (a customer tenant_admin is sent to /403 or sees a load error), so only
// those roles may be shown the cards.
let role = "user";

const Nothing = () => null;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
    };
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user: { id: "dummy", role } }) };
    if (id.includes("lib/auth-hooks")) return { useAccessToken: () => "dummy" };
    if (id.includes("lib/mfa-utils")) return {
        getMfaStatus: async () => ({ enabled: false, verifiedAt: null, recoveryCodesRemaining: 0 }),
        disableMfa: async () => { throw new Error("No credential mutation in this test"); },
        regenerateRecoveryCodes: async () => { throw new Error("No credential mutation in this test"); },
        validateTotpCode: () => false,
    };
    if (id === "@/lib/api" || id.endsWith("/lib/api")) return {
        api: { changePassword: async () => { throw new Error("No credential mutation in this test"); } },
    };
    if (["mfa-setup", "passkey-registration", "passkey-list", "device-list"].some((name) => id.includes(`components/auth/${name}`))) {
        return { default: Nothing, __esModule: true };
    }
    return originalLoad.call(this, id, ...args);
};
let SecurityPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- actual page with scoped external boundaries
    SecurityPage = require("./page").default;
} finally { loader._load = originalLoad; }

beforeEach(() => {
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request"); });
});
afterEach(() => { cleanup(); mock.restoreAll(); });

async function showPageAs(next: string) {
    role = next;
    await act(async () => { render(<SecurityPage />); });
}

test("a tenant_admin is not shown the tenant-wide cards that lead to pages they cannot open", async () => {
    await showPageAs("tenant_admin");
    assert.equal(screen.queryByText("Tenant-wide security"), null);
    assert.equal(screen.queryByText("Audit activity"), null);
    assert.equal(screen.queryByText("Audit and access"), null);
});

for (const adminRole of ["platform_admin", "admin", "partner_admin"]) {
    test(`${adminRole} still sees both tenant-wide cards`, async () => {
        await showPageAs(adminRole);
        assert.ok(screen.getByText("Tenant-wide security"));
        assert.ok(screen.getByText("Audit activity"));
        assert.ok(screen.getByText("Audit and access"));
    });
}

test("an ordinary member sees no tenant-wide cards", async () => {
    await showPageAs("user");
    assert.equal(screen.queryByText("Tenant-wide security"), null);
});
