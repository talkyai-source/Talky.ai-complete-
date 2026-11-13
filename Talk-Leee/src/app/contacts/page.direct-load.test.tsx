/** FX1a Task 1 — a direct load or refresh of /contacts shows its data without any click. */
import Module from "node:module";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { screen } from "@testing-library/react";
import { expectDirectLoadShowsData, restoreDirectLoad } from "@/test-utils/direct-load";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/campaigns/contact-lists")) return { ContactLists: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { ContactCapturedDetails: nothing };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(restoreDirectLoad);

test("direct load of /contacts waits for the sign-in check and then shows the contacts", async () => {
    const sent = await expectDirectLoadShowsData(<Page />, [
        [/\/campaigns\/camp-1\/contacts$/, { items: [{ id: "c-1", campaign_id: "camp-1", phone_number: "+447400123456", status: "pending" }], total: 1, page: 1, page_size: 50 }],
        [/\/campaigns$/, { campaigns: [{ id: "camp-1", name: "Spring push", direction: "outbound" }] }],
    ], () => screen.queryAllByText("+447400123456").length > 0);
    assert.equal(sent.filter((path) => path.endsWith("/campaigns")).length, 1, "the campaign list was asked for once and answered");
});
