/**
 * FX2b Task 7.1 — the Settings profile loads in the shared LoadingState
 * ("Loading profile…"); a profile read that finished without a profile shows
 * the shared ErrorState with Retry, which reads the profile again.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

type Identity = { user: Record<string, unknown> | null; loading: boolean; refreshUser: () => Promise<void> };
let identity: Identity = { user: null, loading: true, refreshUser: async () => {} };

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/auth/device-list")) return { __esModule: true, default: nothing };
    if (id.includes("components/settings/telephony-providers-section")) return { TelephonyProvidersSection: nothing };
    if (id.includes("components/settings/recording-policy-section")) return { RecordingPolicySection: nothing };
    if (id.endsWith("lib/auth-context")) return { useAuth: () => identity };
    if (id.endsWith("lib/auth-hooks")) return { useAccessToken: () => "" };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(() => { cleanup(); identity = { user: null, loading: true, refreshUser: async () => {} }; });
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;

test("profile: loading is a status titled 'Loading profile…'", () => {
    render(<Page />);
    const titled = screen.queryAllByRole("status").some((node) => within(node).queryByText("Loading profile…") !== null);
    assert.equal(titled, true);
});

test("profile: a failed profile read shows the shared ErrorState with Retry, which reads the profile again", () => {
    let reads = 0;
    identity = { user: null, loading: false, refreshUser: async () => { reads += 1; } };
    render(<Page />);
    assert.equal(shown("Loading profile…"), false, "a failed read is not shown as loading");
    assert.equal(screen.getByText("Please retry.").getAttribute("role"), "alert");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(reads, 1);
});

test("profile: a loaded profile still shows the form", () => {
    identity = { user: { id: "u", email: "a@example.test", role: "tenant_admin", name: "Ada", business_name: "Acme" }, loading: false, refreshUser: async () => {} };
    render(<Page />);
    assert.equal(screen.queryByLabelText("Name") !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true);
});
