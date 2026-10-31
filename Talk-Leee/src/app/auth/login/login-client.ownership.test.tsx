import React, { StrictMode, useLayoutEffect } from "react";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { getBrowserAuthToken } from "@/lib/auth-token";
import { ApiClientError, clearFreshLoginGrace, invalidateRequestIdentity } from "@/lib/http-client";
import LoginClientPage from "./login-client";

const originalGetMe = api.getMe, originalLogin = api.login, originalWindow = window;
const originalFetch = globalThis.fetch;
const windowStorage = Object.getOwnPropertyDescriptor(window, "localStorage")!;
const globalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")!;
const originalLocks = Object.getOwnPropertyDescriptor(navigator, "locks");
const response: Awaited<ReturnType<typeof api.login>> = {
  access_token: "synthetic-password-token", token_type: "bearer", user_id: "user-a", tenant_id: "tenant-a",
  email: "a@example.test", role: "tenant_admin", minutes_remaining: 1, minutes_state: "known", message: "",
  mfa_required: false, mfa_challenge_token: undefined, business_name: undefined,
};
let auth: ReturnType<typeof useAuth>;
let client: QueryClient;
function Identity() {
  const value = useAuth(); useLayoutEffect(() => { auth = value; });
  return <span data-testid="identity">{value.user?.id ?? "anonymous"}</span>;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function setup({ strict = false, remember = false, navigationThrows = false } = {}) {
  localStorage.clear(); clearFreshLoginGrace();
  const pending = deferred<typeof response>();
  const seen = { requests: 0, destinations: [] as string[], reloads: 0,
    options: undefined as Parameters<typeof originalLogin>[2] };
  api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "anonymous", url: "/auth/me", method: "GET" }); };
  api.login = async (_email, _password, options) => { seen.requests++; seen.options = options; return pending.promise; };
  globalThis.fetch = async () => { throw new Error("No unapproved request in password ownership test"); };
  const router = { bfcacheId: "synthetic", push: (path: string) => { seen.destinations.push(path); },
    replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } satisfies NonNullable<React.ContextType<typeof AppRouterContext>>;
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const element = <AppRouterContext.Provider value={router}><PathnameContext.Provider value="/auth/login">
    <QueryClientProvider client={client}><AuthProvider><Identity /><LoginClientPage /></AuthProvider></QueryClientProvider>
  </PathnameContext.Provider></AppRouterContext.Provider>;
  const view = render(strict ? <StrictMode>{element}</StrictMode> : element);
  await waitFor(() => assert.equal(auth.loading, false));
  if (remember) fireEvent.click(screen.getByRole("checkbox", { name: "Remember me" }));
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "a@example.test" } });
  fireEvent.submit(screen.getByLabelText("Email").closest("form")!);
  const password = await screen.findByLabelText("Password", {}, { timeout: 3000 });
  fireEvent.change(password, { target: { value: "synthetic-password" } });
  const location = { origin: originalWindow.location.origin, href: originalWindow.location.href,
    search: originalWindow.location.search, pathname: "/auth/login", reload: () => { seen.reloads++; },
    assign: (path: string) => { if (navigationThrows) throw new Error("Synthetic navigation error"); seen.destinations.push(path); } };
  Object.defineProperty(globalThis, "window", { configurable: true, value: new Proxy(originalWindow, {
    get(target, key) { return key === "location" ? location : Reflect.get(target, key); },
  }) });
  return { pending, seen, view, password };
}
async function submit() { await act(async () => { fireEvent.submit(screen.getByLabelText("Password").closest("form")!); }); }
async function settle() { await act(async () => { await Promise.resolve(); }); }
afterEach(() => {
  Object.defineProperty(globalThis, "window", { configurable: true, value: originalWindow });
  Object.defineProperty(originalWindow, "localStorage", windowStorage);
  Object.defineProperty(globalThis, "localStorage", globalStorage);
  cleanup(); client?.clear(); api.getMe = originalGetMe; api.login = originalLogin; globalThis.fetch = originalFetch;
  if (originalLocks) Object.defineProperty(navigator, "locks", originalLocks); else Reflect.deleteProperty(navigator, "locks");
  localStorage.clear(); clearFreshLoginGrace();
});

test("late password success after page unmount cannot write token or navigate", async () => {
  const f = await setup(); await submit(); f.view.unmount();
  assert.equal(f.seen.options?.signal?.aborted, true); assert.equal(f.seen.options?.ownsOperation?.(), false);
  f.pending.resolve(response); await settle();
  assert.equal(getBrowserAuthToken(), null); assert.deepEqual(f.seen.destinations, []);
});

test("password result cannot replace a newer verified identity", async () => {
  const f = await setup(); await submit();
  await act(async () => { auth.applyLoginResult({ user_id: "user-b", tenant_id: "tenant-b", email: "b@example.test",
    role: "tenant_admin", access_token: "newer-b-token" }); });
  f.pending.resolve(response); await settle();
  assert.equal(auth.user?.id, "user-b"); assert.equal(getBrowserAuthToken(), "newer-b-token");
  assert.deepEqual(f.seen.destinations, []);
});

test("identity replacement prevents a late password response from opening its MFA challenge", async () => {
  const f = await setup(); await submit();
  await act(async () => { invalidateRequestIdentity(); });
  f.pending.resolve({ ...response, access_token: "", mfa_required: true, mfa_challenge_token: "old-challenge" });
  await settle();
  assert.equal(screen.queryByRole("heading", { name: "Two-Factor Authentication" }), null);
  assert.deepEqual(f.seen.destinations, []); assert.equal(getBrowserAuthToken(), null);
});

test("same-commit duplicate password submits admit once; pending Change email remains disabled", async () => {
  const f = await setup();
  const form = f.password.closest("form")!;
  await act(async () => { fireEvent.submit(form); fireEvent.submit(form); });
  assert.equal(f.seen.requests, 1);
  const back = screen.getByRole("button", { name: "Change email" });
  assert.equal((back as HTMLButtonElement).disabled, true);
  fireEvent.click(back); assert.ok(screen.getByLabelText("Password"));
  f.pending.resolve(response); await settle();
  assert.equal(auth.user?.id, "user-a"); assert.equal(f.seen.destinations.length, 1);
  await act(async () => { fireEvent.submit(form); }); assert.equal(f.seen.requests, 1);
});

test("StrictMode accepts an owned password result and intentional auth adoption", async () => {
  const f = await setup({ strict: true }); await submit();
  f.pending.resolve(response); await settle();
  assert.equal(auth.user?.id, "user-a"); assert.equal(getBrowserAuthToken(), response.access_token);
  assert.equal(f.seen.destinations.length, 1);
});

test("password MFA response opens exactly the current challenge without token adoption", async () => {
  const f = await setup(); await submit();
  f.pending.resolve({ ...response, access_token: "", mfa_required: true, mfa_challenge_token: "current-challenge" });
  await settle();
  await screen.findByRole("heading", { name: "Two-Factor Authentication" });
  assert.equal(getBrowserAuthToken(), null); assert.deepEqual(f.seen.destinations, []);
});

test("actual password HTTP queue rejects an unmounted owner before dispatch", async () => {
  const f = await setup(); api.login = originalLogin;
  const grant = deferred<void>(); let queued = 0; let fetches = 0;
  Object.defineProperty(navigator, "locks", { configurable: true, value: {
    request: async (_name: string, _options: unknown, run: () => Promise<unknown>) => { queued++; await grant.promise; return run(); },
  } });
  globalThis.fetch = async () => { fetches++; return Response.json(response); };
  await submit(); assert.equal(queued, 1); assert.equal(fetches, 0);
  f.view.unmount(); grant.resolve(); await settle();
  assert.equal(fetches, 0); assert.equal(getBrowserAuthToken(), null); assert.deepEqual(f.seen.destinations, []);
});

test("blocked preference storage cannot interrupt successful cookie-capable sign-in", async () => {
  const f = await setup({ remember: true }); await submit();
  const blocked = { configurable: true, get() { throw new Error("Synthetic storage unavailable"); } };
  Object.defineProperty(originalWindow, "localStorage", blocked); Object.defineProperty(globalThis, "localStorage", blocked);
  f.pending.resolve(response); await settle();
  assert.equal(auth.user?.id, "user-a"); assert.equal(f.seen.destinations.length, 1);
  assert.equal(screen.queryByRole("button", { name: "Reload to check sign-in" }), null);
});

test("post-confirmation navigation failure is visible and offers read recovery without another login", async () => {
  const f = await setup({ navigationThrows: true }); await submit();
  f.pending.resolve(response); await settle();
  assert.equal(auth.user?.id, "user-a");
  assert.match(screen.getByRole("alert").textContent ?? "", /server confirmed sign-in/i);
  fireEvent.click(screen.getByRole("button", { name: "Reload to check sign-in" }));
  assert.equal(f.seen.reloads, 1); await submit(); assert.equal(f.seen.requests, 1);
});
