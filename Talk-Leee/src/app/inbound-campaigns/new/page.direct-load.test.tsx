/** FX1a Task 1 — a direct load or refresh of /inbound-campaigns/new shows the page without any click. */
import Module from "node:module";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { screen } from "@testing-library/react";
import { expectDirectLoadShowsData, restoreDirectLoad } from "@/test-utils/direct-load";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const router = { push() {}, replace() {} };
const params = new URLSearchParams("draft=draft-1");
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => router, useSearchParams: () => params };
    if (id === "next/link") return { __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    // The editors have their own tests; the marker proves the page got past its permission check.
    if (id.includes("components/campaigns/campaign-wizard")) return { CampaignWizard: () => <div data-testid="inbound-step-1" /> };
    if (id.includes("components/campaigns/campaign-form")) return { CampaignForm: nothing };
    if (id.includes("components/inbound/inbound-campaign-form")) return { InboundCampaignForm: nothing };
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

test("direct load of /inbound-campaigns/new waits for the sign-in check and then shows step 1", async () => {
    const sent = await expectDirectLoadShowsData(<Page />, [
        [/\/rbac\/users\/me\/permissions$/, { permissions: ["inbound:manage"] }],
    ], () => screen.queryByTestId("inbound-step-1") !== null);
    assert.equal(screen.queryByText("Permissions could not be verified") === null, true);
    assert.equal(sent.filter((path) => path.endsWith("/rbac/users/me/permissions")).length, 1, "permissions were asked for once and answered");
});
