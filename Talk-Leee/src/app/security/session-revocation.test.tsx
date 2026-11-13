import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// Render the actual page/Section/InfoTip. Replace only unrelated auth, data
// transport and child setup widgets; no real session or credential mutation.
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
        getMfaStatus: async () => ({ enabled: false, verifiedAt: null, recoveryCodesRemaining: 0 }),
        disableMfa: async () => { throw new Error("No credential mutation in copy tests"); },
        regenerateRecoveryCodes: async () => { throw new Error("No credential mutation in copy tests"); },
        validateTotpCode: () => false,
    };
    if (id === "@/lib/api" || id.endsWith("/lib/api")) return {
        api: { changePassword: async () => { throw new Error("No credential mutation in copy tests"); } },
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
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected external request in session-copy test"); });
});
afterEach(() => { cleanup(); mock.restoreAll(); });

async function showPage() {
    await act(async () => { render(<SecurityPage />); });
}

test("active-session sign-out consequence is visible before opening a tooltip", async () => {
    await showPage();
    const section = screen.getByRole("heading", { name: /^Active sessions/ }).closest("section");
    assert.ok(section);
    const description = within(section).getByText(/Everywhere your account is currently signed in/);
    assert.match(description.textContent ?? "", /Revoking signs that device out at its next token refresh\./);
    assert.doesNotMatch(description.textContent ?? "", /does not sign|change your password/i);
    assert.equal(description.closest('[role="tooltip"]'), null);
    assert.equal(screen.queryByRole("tooltip"), null);
});

test("session tooltip reflects current binding instead of claiming seven-day renewal", async () => {
    await showPage();
    fireEvent.click(screen.getByRole("button", { name: "About Active sessions" }));
    const tip = await waitFor(() => screen.getByRole("tooltip"));
    assert.match(tip.textContent ?? "", /Revoked sessions cannot refresh/i);
    assert.match(tip.textContent ?? "", /new requests/i);
    assert.doesNotMatch(tip.textContent ?? "", /not linked|7 days|cannot invalidate|keeps access/i);
});

test("security copy does not promise an unconditional legacy-token cutover time", async () => {
    await showPage();
    const passwordNote = screen.getByText(/Changing your password signs out/);
    assert.match(passwordNote.textContent ?? "", /when they next refresh or make a request\./);
    assert.doesNotMatch(passwordNote.textContent ?? "", /within 15 minutes/i);
});
