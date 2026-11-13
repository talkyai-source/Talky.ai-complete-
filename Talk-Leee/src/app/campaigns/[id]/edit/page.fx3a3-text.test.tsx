/** FX3a3 Task 2.3 — the edit page's title is sentence case. */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import { dashboardApi } from "@/lib/dashboard-api";

// Same boundary stubs as page.states.test.tsx, except that the layout shows its title.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const router = { push() {}, replace() {} };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: "camp-1" }), useRouter: () => router };
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ title, children }: { title?: string; children: React.ReactNode }) => <><h1>{title}</h1>{children}</> };
    }
    if (id.includes("components/campaigns/campaign-form")) return { CampaignForm: () => null };
    if (id.includes("components/campaigns/campaign-basics-editor")) return { CampaignBasicsEditor: () => null };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(() => { cleanup(); mock.restoreAll(); });

test("FX3a3: the edit page title reads Edit campaign", () => {
    mock.method(dashboardApi, "getCampaign", () => new Promise(() => {}));
    render(<Page />);
    assert.equal(screen.queryByRole("heading", { level: 1, name: "Edit campaign" }) !== null, true);
    assert.equal(screen.queryByRole("heading", { level: 1, name: "Edit Campaign" }) !== null, false);
});
