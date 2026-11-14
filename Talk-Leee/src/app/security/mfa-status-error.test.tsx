import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

// The actual Security page, with only its outside boundaries replaced. The
// two-factor status read is the one thing each test controls.
type Status = { enabled: boolean; verifiedAt: string | null; recoveryCodesRemaining: number };
let statusCalls = 0;
let statusAnswer: () => Promise<Status> = async () => ({ enabled: false, verifiedAt: null, recoveryCodesRemaining: 0 });

const Nothing = () => null;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
    };
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user: { id: "synthetic-user", role: "user" } }) };
    if (id.includes("lib/auth-hooks")) return { useAccessToken: () => "synthetic-token" };
    if (id.includes("lib/mfa-utils")) return {
        getMfaStatus: async () => { statusCalls += 1; return statusAnswer(); },
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
    statusCalls = 0;
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request in the two-factor status test"); });
});
afterEach(() => { cleanup(); mock.restoreAll(); });

async function showPage() {
    await act(async () => { render(<SecurityPage />); });
}

test("a failed status check says so and offers a retry, instead of showing two-factor as off", async () => {
    statusAnswer = async () => { throw new Error("status unavailable"); };
    await showPage();

    const message = await screen.findByText(/could not check whether two-factor authentication is on/i);
    assert.ok(message.closest('[role="alert"]'), "the failure is announced");
    assert.equal(screen.queryByText("Not enabled"), null, "an unknown status is not shown as off");
    assert.equal(screen.queryByRole("button", { name: "Enable two-factor authentication" }), null,
        "a user who may already have two-factor on is not invited to set it up again");
    assert.equal(screen.queryByText("Two-factor authentication is on"), null);

    // The retry reads the status again and shows the real answer.
    statusAnswer = async () => ({ enabled: true, verifiedAt: null, recoveryCodesRemaining: 8 });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Try again" })); });
    assert.ok(await screen.findByText("Two-factor authentication is on"));
    assert.equal(screen.queryByText(/could not check whether two-factor/i), null);
    assert.equal(statusCalls, 2);
});

test("an account that really has no two-factor still gets the set-up button", async () => {
    statusAnswer = async () => ({ enabled: false, verifiedAt: null, recoveryCodesRemaining: 0 });
    await showPage();
    assert.ok(await screen.findByText("Not enabled"));
    assert.ok(screen.getByRole("button", { name: "Enable two-factor authentication" }));
    assert.equal(screen.queryByText(/could not check whether two-factor/i), null);
});
