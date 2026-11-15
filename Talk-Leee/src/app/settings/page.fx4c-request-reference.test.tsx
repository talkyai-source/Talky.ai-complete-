/**
 * FX4c Task A1 — the Settings profile error state shows the reference of the
 * profile read that failed, which the auth provider now keeps.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { api } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { ApiClientError } from "@/lib/http-client";
import { failedReadRequestId } from "@/components/states/page-states";

type Identity = { user: Record<string, unknown> | null; loading: boolean; refreshUser: () => Promise<void>; profileReadError?: unknown };
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

const SLOW = 15_000;
const getMe = api.getMe;
afterEach(() => { cleanup(); api.getMe = getMe; localStorage.clear(); identity = { user: null, loading: true, refreshUser: async () => {} }; });
const shown = (text: string | RegExp) => screen.queryByText(text) !== null;
const failedRead = (requestId: string) => new ApiClientError({
    status: 503, code: "service_unavailable", message: "Profile store is unavailable.", requestId,
    url: "http://example.test/auth/me", method: "GET",
});

test("profile: the failed read's request reference is shown", () => {
    identity = { user: null, loading: false, refreshUser: async () => {}, profileReadError: failedRead("req-fx4c-profile") };
    render(<Page />);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown("Request reference: req-fx4c-profile"), true);
});

test("profile: a failed read without a reference shows no reference line", () => {
    identity = { user: null, loading: false, refreshUser: async () => {} };
    render(<Page />);
    assert.equal(screen.queryByRole("button", { name: "Retry" }) !== null, true);
    assert.equal(shown(/Request reference/), false);
});

function Probe() {
    const { loading, user, profileReadError, refreshUser } = useAuth();
    return (
        <div>
            <span data-testid="state">{loading ? "loading" : user ? "user" : "none"}</span>
            <span data-testid="reference">{failedReadRequestId(profileReadError) ?? "-"}</span>
            <button type="button" onClick={() => void refreshUser()}>read again</button>
        </div>
    );
}

test("auth provider: keeps the failed profile read and clears it once a read succeeds", async () => {
    let fail = true;
    api.getMe = (async () => {
        if (fail) throw failedRead("req-fx4c-me");
        return { id: "u-1", email: "owner@example.test", role: "tenant_admin", name: "Owner", business_name: "Acme" };
    }) as unknown as typeof api.getMe;
    render(<AuthProvider><Probe /></AuthProvider>);
    await waitFor(() => assert.equal(screen.getByTestId("reference").textContent, "req-fx4c-me"), { timeout: SLOW });
    assert.equal(screen.getByTestId("state").textContent, "none");

    fail = false;
    screen.getByRole("button", { name: "read again" }).click();
    await waitFor(() => assert.equal(screen.getByTestId("state").textContent, "user"), { timeout: SLOW });
    assert.equal(screen.getByTestId("reference").textContent, "-");
});
