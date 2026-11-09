import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

// QA F1-21 (QA-UI-041): the Connectors page offered a read-only user every
// connect, disconnect and Salesforce settings action. It now passes what the
// effective permissions allow to each card, and shows the Salesforce
// settings (all connectors:update) only to roles that may change them.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let permissionsQuery: { isSuccess: boolean; data?: { permissions: string[] } } = { isSuccess: false };
const cards: Array<{ type: string; mayConnect?: boolean; mayDisconnect?: boolean }> = [];
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useRouter: () => ({ replace() {}, push() {} }), useSearchParams: () => new URLSearchParams() };
    if (id === "@tanstack/react-query") return { useQueryClient: () => ({ invalidateQueries: async () => undefined }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("lib/api-hooks")) return {
        queryKeys: { connectorStatuses: () => ["connectors"] },
        useConnectorStatuses: () => ({ isLoading: false, isError: false, data: { items: [
            { type: "email", status: "connected" }, { type: "salesforce", status: "connected" },
        ] } }),
    };
    if (id.includes("components/connectors/connector-card")) return {
        ConnectorCard: (props: { type: string; mayConnect?: boolean; mayDisconnect?: boolean }) => {
            cards.push({ type: props.type, mayConnect: props.mayConnect, mayDisconnect: props.mayDisconnect });
            return null;
        },
    };
    if (id.includes("components/connectors/salesforce-settings")) return {
        SalesforceSettingsPanel: () => <div data-testid="salesforce-settings" />,
    };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissionsQuery };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(() => { cleanup(); cards.splice(0); permissionsQuery = { isSuccess: false }; });

function renderAs(permissions: string[] | null) {
    permissionsQuery = permissions ? { isSuccess: true, data: { permissions } } : { isSuccess: false };
    return render(<Page />);
}

test("a read-only role gets cards without connect or disconnect, and no Salesforce settings", () => {
    const { queryByTestId } = renderAs(["connectors:read", "campaigns:read"]);
    const email = cards.find((card) => card.type === "email");
    assert.ok(email, "email card rendered");
    assert.equal(email.mayConnect, false);
    assert.equal(email.mayDisconnect, false);
    assert.equal(queryByTestId("salesforce-settings") === null, true, "Salesforce settings shown");
});

test("a role that manages connectors keeps every action and the Salesforce settings", () => {
    const { getByTestId } = renderAs(["connectors:create", "connectors:read", "connectors:update", "connectors:delete"]);
    const email = cards.find((card) => card.type === "email");
    assert.equal(email?.mayConnect, true);
    assert.equal(email?.mayDisconnect, true);
    assert.ok(getByTestId("salesforce-settings"));
});

test("while permissions are unknown every action stays offered", () => {
    const { getByTestId } = renderAs(null);
    assert.equal(cards.find((card) => card.type === "email")?.mayConnect, true);
    assert.ok(getByTestId("salesforce-settings"));
});
