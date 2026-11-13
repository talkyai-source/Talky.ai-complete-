/** FX3a Task 5.1 — the Billing page title is the menu name. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

// Only the title is under test: the layout shows it, the page's own panels are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ title, children }: { title?: string; children: React.ReactNode }) => <><h1>{title}</h1>{children}</> };
    }
    if (id.includes("components/billing/billing-overview")) return { BillingOverview: nothing };
    if (id.includes("components/billing/topup-card")) return { TopupCard: nothing };
    if (id.includes("components/billing/billing-plan-selector")) return { BillingCheckoutReturn: nothing };
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user: null }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(() => { cleanup(); });

test("FX3a: the Billing page title is Billing, the name the menu uses", () => {
    render(<Page />);
    assert.equal(screen.queryByRole("heading", { level: 1, name: "Billing" }) !== null, true);
    assert.equal(screen.queryByText("Billing & usage") !== null, false);
});
