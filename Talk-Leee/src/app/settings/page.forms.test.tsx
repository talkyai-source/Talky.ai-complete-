/**
 * Forms audit B-F12 and B-F19. Same boundary stubs as page.fx3b-text.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

let role = "tenant_admin";
const settingsCalls: Array<Record<string, unknown>> = [];

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/auth/device-list")) return { __esModule: true, default: nothing };
    if (id.includes("components/settings/telephony-providers-section")) return { TelephonyProvidersSection: nothing };
    if (id.includes("components/settings/recording-policy-section")) return { RecordingPolicySection: nothing };
    if (id.endsWith("lib/auth-context")) {
        return { useAuth: () => ({ user: { id: "u", email: "a@example.test", role, name: "Ada", business_name: "Acme" }, loading: false, refreshUser: async () => {} }) };
    }
    if (id.endsWith("lib/auth-hooks")) return { useAccessToken: () => "" };
    if (id.endsWith("lib/notifications-client")) {
        const real = originalLoad.call(this, id, ...args) as {
            useNotificationsState: () => Record<string, unknown>;
            useNotificationsActions: () => Record<string, unknown>;
        };
        return {
            ...real,
            useNotificationsState: () => ({ ...real.useNotificationsState(), hydrated: true }),
            useNotificationsActions: () => ({ ...real.useNotificationsActions(), setSettings: (patch: Record<string, unknown>) => { settingsCalls.push(patch); } }),
        };
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

afterEach(() => { cleanup(); role = "tenant_admin"; settingsCalls.length = 0; });

test("B-F12: the duration box can be typed key by key; it clamps only on blur", () => {
    render(<Page />);
    const box = screen.getByLabelText("Notification display duration (ms)") as HTMLInputElement;
    let typed = "";
    for (const ch of "5000") {
        typed += ch;
        fireEvent.change(box, { target: { value: typed } });
        assert.equal(box.value, typed);
    }
    assert.deepEqual(settingsCalls, [], "nothing is clamped while typing");
    fireEvent.blur(box);
    assert.deepEqual(settingsCalls, [{ toastDurationMs: 5000 }]);
});

test("B-F12: a too-small value is clamped to 1200 on blur", () => {
    render(<Page />);
    const box = screen.getByLabelText("Notification display duration (ms)") as HTMLInputElement;
    fireEvent.change(box, { target: { value: "5" } });
    assert.equal(box.value, "5");
    fireEvent.blur(box);
    assert.deepEqual(settingsCalls, [{ toastDurationMs: 1200 }]);
});

test("B-F19: a non-admin cannot edit Business name; names are limited to 100", () => {
    role = "member";
    render(<Page />);
    const business = screen.getByLabelText("Business name") as HTMLInputElement;
    assert.equal(business.disabled, true);
    assert.equal(business.readOnly, true);
    assert.equal((screen.getByLabelText("Name") as HTMLInputElement).maxLength, 100);
    assert.equal(business.maxLength, 100);
});

test("B-F19: a tenant admin can edit Business name", () => {
    render(<Page />);
    assert.equal((screen.getByLabelText("Business name") as HTMLInputElement).disabled, false);
});
