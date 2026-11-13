/** FX3a2 Task 2 — the create page's title is sentence case. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

// Only the title is under test: the layout shows it, the wizard and the form are stubbed.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ push() {}, replace() {}, back() {} }) };
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ title, children }: { title?: string; children: React.ReactNode }) => <><h1>{title}</h1>{children}</> };
    }
    if (id.includes("components/campaigns/campaign-form")) return { CampaignForm: nothing };
    if (id.includes("components/campaigns/campaign-wizard")) return { CampaignWizard: nothing };
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

test("FX3a2: the create page title reads Create campaign", () => {
    render(<Page />);
    assert.equal(screen.queryByRole("heading", { level: 1, name: "Create campaign" }) !== null, true);
    assert.equal(screen.queryByRole("heading", { level: 1, name: "Create Campaign" }) !== null, false);
});
