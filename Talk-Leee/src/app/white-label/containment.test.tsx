// Exercise real server route gates in Next request scopes; only the shared
// dashboard chrome is replaced so unrelated client providers do not mask them.
import "next/dist/server/node-environment-baseline";
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { NextRequest } from "next/server";
import { createRequestStoreForAPI } from "next/dist/server/async-storage/request-store";
import { workAsyncStorage, type WorkStore } from "next/dist/server/app-render/work-async-storage.external";
import { workUnitAsyncStorage } from "next/dist/server/app-render/work-unit-async-storage.external";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ title, children }: { title: string; children: React.ReactNode }) => <main><h1>{title}</h1>{children}</main>,
    };
    return originalLoad.call(this, id, ...args);
};
type PartnerProps = { children: React.ReactNode; params: Promise<{ partner: string }> };
let tenantsBoundary: (props: PartnerProps) => Promise<React.ReactNode>;
let billingBoundary: (props: PartnerProps) => Promise<React.ReactNode>;
let overview: (props: PartnerProps) => Promise<React.ReactNode>;
let analytics: (props: PartnerProps) => Promise<React.ReactNode>;
let workspace: () => React.ReactNode;
let workspaceBoundary: (props: { children: React.ReactNode }) => Promise<React.ReactNode>;
let agentSettings: () => React.ReactNode;
let preview: React.ComponentType;
let brandBoundary: (props: PartnerProps) => Promise<React.ReactNode>;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped dashboard chrome boundary only
    tenantsBoundary = require("./[partner]/tenants/layout").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    billingBoundary = require("./[partner]/billing/layout").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    overview = require("./[partner]/dashboard/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    analytics = require("./[partner]/analytics/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    workspace = require("./dashboard/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    workspaceBoundary = require("./dashboard/layout").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    agentSettings = require("./[partner]/tenants/[tenant]/agent-settings/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    preview = require("./[partner]/preview/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    brandBoundary = require("./[partner]/layout").default;
} finally {
    loader._load = originalLoad;
}

const originalFetch = globalThis.fetch;
const previous = { NODE_ENV: process.env.NODE_ENV, NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL };
let principal: string | null;
let calls: Array<{ url: string; method: string }>;
beforeEach(() => {
    Object.assign(process.env, { NODE_ENV: "production", NEXT_PUBLIC_API_BASE_URL: "https://backend.example/api/v1" });
    principal = "white_label_admin";
    calls = [];
    window.localStorage.setItem("white_label:acme:tenants:v1", JSON.stringify([
        { id: "synthetic-local-id", name: "Browser-only tenant", status: "suspended", minutesAllocated: 9000 },
    ]));
    globalThis.fetch = async (url, init) => {
        calls.push({ url: String(url), method: init?.method ?? "GET" });
        assert.equal(String(url), "https://backend.example/api/v1/auth/me", "No reseller data or mutation request is permitted");
        return principal ? Response.json({ id: "synthetic-principal", email: "operator@example.invalid", role: principal })
            : new Response(null, { status: 401 });
    };
});
afterEach(() => {
    cleanup();
    window.localStorage.clear();
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
    }
});

function request<T>(path: string, operation: () => T, cookie = "talky_at=synthetic; talky_sid=synthetic-session") {
    const req = new NextRequest(`https://frontend.example${path}`, { headers: { cookie } });
    const store = createRequestStoreForAPI(req, req.nextUrl, { tags: [], expirationsByCacheKind: new Map() }, undefined, undefined, undefined);
    store.phase = "render";
    return workAsyncStorage.run({ route: path } as WorkStore, () => workUnitAsyncStorage.run(store, operation));
}

function UnsafeEditor() {
    throw new Error("An operational child must never mount through containment");
}
const props = () => ({ params: Promise.resolve({ partner: "acme" }), children: <UnsafeEditor /> });

for (const [path, route] of [
    ["/white-label/acme/tenants", tenantsBoundary],
    ["/white-label/acme/tenants/synthetic-local-id/agent-settings", tenantsBoundary],
    ["/white-label/acme/billing", billingBoundary],
    ["/white-label/acme/dashboard", overview],
    ["/white-label/acme/analytics", analytics],
] as const) {
    test(`direct route ${path} displays containment without mounting an editor or fetching reseller data`, async () => {
        const content = await request(path, () => route(props()));
        const { container } = render(content);
        assert.ok(screen.getByRole("heading", { name: "Reseller operations are not included in this release" }));
        assert.ok(screen.getByText("Staff invitations are also not included in this release."));
        assert.equal(screen.getByRole("link", { name: "View branding preview" }).getAttribute("href"), "/white-label/acme/preview");
        assert.equal(container.querySelectorAll("input,button,form,table").length, 0);
        assert.doesNotMatch(container.textContent ?? "", /INV-1023|Browser-only tenant|cannot place calls|\$320|3,200/);
        assert.deepEqual(calls, [{ url: "https://backend.example/api/v1/auth/me", method: "GET" }]);
    });
}

for (const role of ["tenant_admin", "partner_admin", "platform_admin"]) {
    test(`existing server role boundary refuses ${role} on a nested tenant URL`, async () => {
        principal = role;
        await assert.rejects(request("/white-label/acme/tenants/any-id/agent-settings", () => tenantsBoundary(props())),
            (error: unknown) => Boolean(error && typeof error === "object" && "digest" in error && String(error.digest).includes("/403")));
    });
}

test("anonymous direct billing URL requires login and subsequent requests observe revocation", async () => {
    await assert.rejects(request("/white-label/acme/billing", () => billingBoundary(props()), ""),
        (error: unknown) => Boolean(error && typeof error === "object" && "digest" in error && String(error.digest).includes("/auth/login")));
    assert.equal(calls.length, 0);
    await request("/white-label/acme/billing", () => billingBoundary(props()));
    principal = null;
    await assert.rejects(request("/white-label/acme/billing", () => billingBoundary(props())),
        (error: unknown) => Boolean(error && typeof error === "object" && "digest" in error && String(error.digest).includes("/auth/login")));
    assert.equal(calls.length, 2);
});

test("workspace retains its gate and preview links without partner creation or generated token controls", async () => {
    const content = await request("/white-label/dashboard", () => workspaceBoundary({ children: workspace() }));
    const { container } = render(content);
    assert.ok(screen.getByRole("heading", { name: "Reseller operations are not included in this release" }));
    assert.equal(container.querySelectorAll("input,button,form,table").length, 0);
    assert.deepEqual(Array.from(container.querySelectorAll("a")).map(link => link.getAttribute("href")),
        ["/white-label/acme/preview", "/white-label/zen/preview"]);
    assert.equal(agentSettings(), null);
});

test("branding preview still renders the existing partner identity and cosmetic controls", async () => {
    const Preview = preview;
    const content = await brandBoundary({ params: Promise.resolve({ partner: "zen" }), children: <Preview /> });
    render(content);
    assert.ok(screen.getByRole("heading", { name: "Zen Preview" }));
    assert.ok(screen.getByRole("button", { name: "Primary Button" }));
    assert.equal(screen.queryByText("Reseller operations are not included in this release"), null);
    assert.equal(calls.length, 0);
});
