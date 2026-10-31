import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React, { useLayoutEffect } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { clearFreshLoginGrace } from "@/lib/http-client";
import LogoutButton from "./logout-button";
import { Sidebar } from "@/components/layout/sidebar";

const originalGetMe = api.getMe;
const originalLogout = api.logout;
const originalWindow = window;
const account = {
  id: "synthetic-a", tenant_id: "tenant-a", email: "a@example.test", role: "tenant_admin", minutes_remaining: 10, minutes_state: "known",
  name: undefined, business_name: undefined, partner_id: undefined, partner_status: undefined,
  tenant_status: undefined, suspended_scope: undefined, suspension_reason: undefined, suspended_at: undefined,
} satisfies Awaited<ReturnType<typeof api.getMe>>;
let auth: ReturnType<typeof useAuth>;
let client: QueryClient;
let destinations: string[] = [];
let completed = 0;
let errors: string[] = [];
function CurrentIdentity() {
  const current = useAuth();
  useLayoutEffect(() => { auth = current; });
  return <span data-testid="identity">{current.user?.id ?? "anonymous"}</span>;
}
async function mount(kind: "button" | "sidebar") {
  destinations = []; completed = 0; errors = [];
  originalWindow.requestAnimationFrame = callback => originalWindow.setTimeout(() => callback(0), 0);
  originalWindow.cancelAnimationFrame = id => originalWindow.clearTimeout(id);
  localStorage.setItem("talklee.auth.token", "synthetic-access");
  api.getMe = async () => ({ ...account });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(["private-a"], "synthetic private value");
  const router = { bfcacheId: "synthetic", push: (path: string) => { destinations.push(path); }, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } satisfies NonNullable<React.ContextType<typeof AppRouterContext>>;
  render(<AppRouterContext.Provider value={router}><PathnameContext.Provider value="/dashboard/">
    <QueryClientProvider client={client}><AuthProvider><CurrentIdentity />
      {kind === "button" ? <LogoutButton token="obsolete-prop" onLogoutComplete={() => { completed++; }} onError={error => errors.push(error)} /> : <Sidebar />}
    </AuthProvider></QueryClientProvider>
  </PathnameContext.Provider></AppRouterContext.Provider>);
  await waitFor(() => assert.equal(screen.getByTestId("identity").textContent, account.id));
  // Intercept only navigation, leaving real DOM, storage and AuthProvider behavior.
  const location = { origin: originalWindow.location.origin, set href(value: string) { destinations.push(value); } };
  Object.defineProperty(globalThis, "window", { configurable: true, value: new Proxy(originalWindow, { get(target, key) { return key === "location" ? location : Reflect.get(target, key); } }) });
}
function clickLogout(kind: "button" | "sidebar") {
  const buttons = screen.getAllByRole("button", { name: kind === "button" ? "Sign out" : /logs*out|signs*out/i });
  fireEvent.click(buttons[0]);
}
afterEach(() => {
  Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
  cleanup(); client?.clear(); api.getMe = originalGetMe; api.logout = originalLogout;
  localStorage.clear(); clearFreshLoginGrace();
});

for (const kind of ["button", "sidebar"] as const) {
  test(`${kind} uses AuthContext and distinguishes confirmed versus unconfirmed server logout`, async () => {
    let calls = 0;
    api.logout = async () => { calls++; throw new Error("synthetic server unavailable"); };
    await mount(kind);
    await act(async () => { clickLogout(kind); });
    await waitFor(() => assert.equal(destinations.length, 1), { timeout: 3000 });
    assert.ok(destinations[0].endsWith("/auth/login?logout=unconfirmed"));
    assert.equal(screen.getByTestId("identity").textContent, "anonymous");
    assert.equal(calls, 1);
    assert.notEqual(localStorage.getItem("talky.logout.pending"), null);
    if (kind === "button") { assert.equal(completed, 0); assert.equal(errors.length, 1); }
  });

  test(`${kind} confirmed logout navigates only after acknowledgment`, async () => {
    let resolve!: () => void;
    api.logout = () => new Promise<void>(yes => { resolve = yes; });
    await mount(kind);
    act(() => { clickLogout(kind); });
    assert.equal(destinations.length, 0);
    await act(async () => { resolve(); });
    await waitFor(() => assert.equal(destinations.length, 1));
    assert.ok(destinations[0].endsWith("/auth/login"));
    if (kind === "button") assert.equal(completed, 1);
  });

  test(`${kind} delayed A logout cannot navigate or clear a newly logged-in B`, async () => {
    let resolve!: () => void;
    api.logout = () => new Promise<void>(yes => { resolve = yes; });
    await mount(kind);
    act(() => { clickLogout(kind); });
    act(() => { auth.applyLoginResult({ user_id: "synthetic-b", tenant_id: "tenant-b", email: "b@example.test", role: "tenant_admin", access_token: "synthetic-b-token" }); });
    client.setQueryData(["private-b"], "B must survive");
    await act(async () => { resolve(); });
    assert.equal(screen.getByTestId("identity").textContent, "synthetic-b");
    assert.equal(destinations.length, 0);
    assert.equal(client.getQueryData(["private-b"]), "B must survive");
    assert.equal(completed, 0);
  });
}
