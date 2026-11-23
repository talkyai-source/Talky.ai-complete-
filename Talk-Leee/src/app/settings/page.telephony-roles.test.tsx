/**
 * Settings -> Telephony. Every /telephony/sip route is refused by the server
 * for anyone who is not an account admin, so such a role sees a plain note and
 * the page makes no telephony request; the admin roles still get the section.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "@/lib/api";

type Identity = { user: Record<string, unknown> | null; loading: boolean; refreshUser: () => Promise<void> };
let identity: Identity = { user: null, loading: true, refreshUser: async () => {} };

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/auth/device-list")) return { __esModule: true, default: nothing };
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

const request = api.request;
const clients: QueryClient[] = [];
afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    api.request = request;
    identity = { user: null, loading: true, refreshUser: async () => {} };
});

const NOTE = "Phone lines are managed by your account admin. Ask an admin if a line needs to be added or changed.";

function mount(role: string | null, loading = false) {
    const paths: string[] = [];
    api.request = async <T,>(options: Parameters<typeof api.request>[0]) => {
        paths.push(options.path);
        if (options.path === "/telephony/providers") return { active: "none", availability: {}, providers: [] } as T;
        return [] as T;
    };
    identity = { user: role ? { id: "u", email: "a@example.test", role, name: "Ada", business_name: "Acme" } : null, loading, refreshUser: async () => {} };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><Page /></QueryClientProvider>);
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Telephony" }), { button: 0, ctrlKey: false });
    return paths;
}
const telephonyPaths = (paths: string[]) => paths.filter((path) => path.includes("/telephony/"));

for (const role of ["readonly", "user", "agent", "billing_user", "campaign_manager"]) {
    test(`telephony tab: ${role} sees the note and no telephony request is made`, async () => {
        const paths = mount(role);
        await screen.findByText(NOTE);
        await new Promise((resolve) => setTimeout(resolve, 20));
        assert.deepEqual(telephonyPaths(paths), []);
        assert.equal(screen.queryByRole("button", { name: "Retry" }) === null, true);
        assert.equal(screen.queryByText("Failed to load telephony settings") === null, true);
    });
}

for (const role of ["tenant_admin", "partner_admin", "platform_admin"]) {
    test(`telephony tab: ${role} gets the section and its reads are made`, async () => {
        const paths = mount(role);
        await waitFor(() => assert.equal(telephonyPaths(paths).length > 0, true));
        assert.equal(screen.queryByText(NOTE) === null, true);
    });
}

test("telephony tab: while the person is loading there is no note and no telephony request", async () => {
    const paths = mount(null, true);
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(screen.queryByText(NOTE) === null, true);
    assert.deepEqual(telephonyPaths(paths), []);
});

// The Buy Now card (components/settings/telephony-buy-card.tsx) is for every signed-in person.
const buyCard = () => document.querySelector<HTMLElement>("[data-telephony-buy-card]");
const isLastInTheTab = (card: HTMLElement) => card.nextElementSibling === null && card.parentElement?.nextElementSibling === null && card.parentElement?.parentElement?.getAttribute("role") === "tabpanel";

for (const role of ["readonly", "user", "agent", "billing_user", "campaign_manager"]) {
    test(`telephony tab: ${role} sees the Buy Now card directly after the note`, async () => {
        mount(role);
        const note = (await screen.findByText(NOTE)).parentElement as HTMLElement;
        const card = buyCard();
        assert.equal(card !== null, true);
        assert.equal(note.nextElementSibling === card, true);
        assert.equal(isLastInTheTab(card as HTMLElement), true);
        assert.equal(screen.getAllByRole("button", { name: "Buy Now" }).length, 1);
        // The rest of the section stays closed to this role.
        assert.equal(screen.queryByRole("heading", { name: "Your PBX" }) === null, true);
    });
}

for (const role of ["tenant_admin", "partner_admin", "platform_admin"]) {
    test(`telephony tab: ${role} sees the Buy Now card last, after the whole section`, async () => {
        mount(role);
        const pbx = await screen.findByRole("heading", { name: "Your PBX" });
        const card = buyCard();
        assert.equal(card !== null, true);
        assert.equal(isLastInTheTab(card as HTMLElement), true);
        const section = (card as HTMLElement).previousElementSibling as HTMLElement;
        assert.equal(section.contains(pbx), true);
        assert.equal(section.contains(screen.getByRole("heading", { name: "Twilio" })), true);
        assert.equal(section.contains(card), false);
        assert.equal(screen.getAllByRole("button", { name: "Buy Now" }).length, 1);
        assert.equal(screen.queryByText(NOTE) === null, true);
    });
}

test("telephony tab: while the person is loading there is no Buy Now card", async () => {
    mount(null, true);
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(buyCard() === null, true);
});
