/**
 * FX3b Task 6.1 — the notification priority options are written in sentence
 * case ("Low", "Normal", "High") like the routing options beside them.
 * Same boundary stubs as page.states.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const identity = { user: { id: "u", email: "a@example.test", role: "tenant_admin", name: "Ada", business_name: "Acme" }, loading: false, refreshUser: async () => {} };

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
    if (id.endsWith("lib/notifications-client")) {
        // The selects are disabled until the notification store is ready; ready here.
        const real = originalLoad.call(this, id, ...args) as { useNotificationsState: () => Record<string, unknown> };
        return { ...real, useNotificationsState: () => ({ ...real.useNotificationsState(), hydrated: true }) };
    }
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const originalScrollIntoView = window.Element.prototype.scrollIntoView;
beforeEach(() => { window.Element.prototype.scrollIntoView = () => {}; });
afterEach(() => { cleanup(); window.Element.prototype.scrollIntoView = originalScrollIntoView; });

test("FX3b 6.1: the priority options are 'Low', 'Normal' and 'High'", () => {
    render(<Page />);
    const select = screen.getByRole("combobox", { name: "success priority" });
    assert.equal(["Low", "Normal", "High"].some((label) => select.textContent?.includes(label)), true, select.textContent ?? "");
    fireEvent.click(select);
    const options = within(screen.getByRole("listbox")).getAllByRole("option").map((option) => option.textContent?.trim());
    assert.deepEqual(options, ["Low", "Normal", "High"]);
    // The routing options beside them, for the same case.
    const routing = screen.getByRole("combobox", { name: "success routing" });
    assert.equal(["In-app popup", "History only"].some((label) => routing.textContent?.includes(label)), true, routing.textContent ?? "");
});
