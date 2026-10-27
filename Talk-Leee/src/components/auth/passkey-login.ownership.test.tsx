import React, { StrictMode, useState } from "react";
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AnimatePresence, usePresence } from "framer-motion";
import { api } from "@/lib/api";
import { invalidateRequestIdentity } from "@/lib/http-client";
import PasskeyLogin from "./passkey-login";

const originalBegin = api.beginPasskeyLogin;
const originalComplete = api.completePasskeyLogin;
const originalFetch = globalThis.fetch;
const savedDescriptors = [
  [navigator, "credentials"], [navigator, "locks"], [window, "PublicKeyCredential"], [globalThis, "PublicKeyCredential"],
].map(([target, key]) => ({ target: target as object, key: key as string,
  descriptor: Object.getOwnPropertyDescriptor(target, key as string) }));

class SyntheticCredential {
  id = "owned-key";
  rawId = new Uint8Array([1]).buffer;
  type = "public-key";
  response = { clientDataJSON: new Uint8Array([2]).buffer, authenticatorData: new Uint8Array([3]).buffer,
    signature: new Uint8Array([4]).buffer, userHandle: null };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const startResult = { ceremony_id: "owned-ceremony", has_passkeys: true,
  options: { challenge: "AQI", rpId: "example.test", userVerification: "required", timeout: 42000 } };
const loginResult: Awaited<ReturnType<typeof api.completePasskeyLogin>> = {
  access_token: "synthetic-owned-token", token_type: "bearer", user_id: "user-a", tenant_id: "tenant-a",
  email: "a@example.test", role: "tenant_admin", minutes_remaining: 1, minutes_state: "known", message: "",
  mfa_required: false, mfa_challenge_token: undefined, business_name: undefined,
};

function setup(phase: "begin" | "credential" | "complete") {
  const begin = deferred<typeof startResult>();
  const credential = deferred<SyntheticCredential>();
  const complete = deferred<typeof loginResult>();
  const seen = { begin: 0, credential: 0, complete: 0, success: 0, errors: [] as string[],
    credentialOptions: null as CredentialRequestOptions | null };
  api.beginPasskeyLogin = async () => { seen.begin++; return phase === "begin" ? begin.promise : startResult; };
  api.completePasskeyLogin = async () => { seen.complete++; return phase === "complete" ? complete.promise : loginResult; };
  Object.defineProperty(window, "PublicKeyCredential", { configurable: true, value: SyntheticCredential });
  Object.defineProperty(globalThis, "PublicKeyCredential", { configurable: true, value: SyntheticCredential });
  Object.defineProperty(navigator, "credentials", { configurable: true, value: {
    create: async () => new SyntheticCredential(),
    get: async (options: CredentialRequestOptions) => {
      seen.credential++; seen.credentialOptions = options;
      return phase === "credential" ? credential.promise : new SyntheticCredential();
    },
  } });
  const props = { onSuccess: () => { seen.success++; }, onError: (error: string) => { seen.errors.push(error); } };
  return { begin, credential, complete, seen, props };
}

async function clickPasskey() {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Sign in with Passkey/i })); });
}
async function settle() { await act(async () => { await Promise.resolve(); }); }

afterEach(() => {
  cleanup(); api.beginPasskeyLogin = originalBegin; api.completePasskeyLogin = originalComplete;
  globalThis.fetch = originalFetch;
  for (const { target, key, descriptor } of savedDescriptors) {
    if (descriptor) Object.defineProperty(target, key, descriptor); else Reflect.deleteProperty(target, key);
  }
  invalidateRequestIdentity();
});

test("late begin after unmount cannot open the native prompt", async () => {
  const fixture = setup("begin");
  const view = render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey(); assert.equal(fixture.seen.begin, 1);
  view.unmount(); fixture.begin.resolve(startResult); await settle();
  assert.equal(fixture.seen.credential, 0);
  assert.equal(fixture.seen.complete, 0); assert.equal(fixture.seen.success, 0);
});

test("late native credential after unmount cannot complete login", async () => {
  const fixture = setup("credential");
  const view = render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey(); assert.equal(fixture.seen.credential, 1);
  view.unmount(); assert.equal(fixture.seen.credentialOptions?.signal?.aborted, true);
  fixture.credential.resolve(new SyntheticCredential()); await settle();
  assert.equal(fixture.seen.complete, 0); assert.equal(fixture.seen.success, 0);
});

test("late completion after unmount cannot adopt a login", async () => {
  const fixture = setup("complete");
  const view = render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey(); assert.equal(fixture.seen.complete, 1);
  view.unmount(); fixture.complete.resolve(loginResult); await settle();
  assert.equal(fixture.seen.success, 0);
});

test("identity replacement during the native prompt prevents completion", async () => {
  const fixture = setup("credential");
  render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey();
  await act(async () => { invalidateRequestIdentity(); });
  assert.equal(fixture.seen.credentialOptions?.signal?.aborted, true);
  fixture.credential.resolve(new SyntheticCredential()); await settle();
  assert.equal(fixture.seen.complete, 0); assert.equal(fixture.seen.success, 0);
});

test("identity replacement during completion prevents late adoption", async () => {
  const fixture = setup("complete");
  render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey();
  await act(async () => { invalidateRequestIdentity(); });
  fixture.complete.resolve(loginResult); await settle();
  assert.equal(fixture.seen.success, 0);
});

// Keep the exiting child mounted, as the real login motion.div does until its
// exit animation completes. This tests real AnimatePresence context, not a
// mocked ownership hook or an immediate unmount substitute for Back.
function ExitBarrier({ children }: { children: React.ReactNode }) {
  usePresence();
  return <div>{children}</div>;
}
function BackHarness({ props }: { props: React.ComponentProps<typeof PasskeyLogin> }) {
  const [visible, setVisible] = useState(true);
  return <><button onClick={() => setVisible(false)}>Back to email</button>
    <AnimatePresence>{visible && <ExitBarrier key="passkey"><PasskeyLogin {...props} /></ExitBarrier>}</AnimatePresence></>;
}

test("Back retires a native prompt before the exiting component unmounts", async () => {
  const fixture = setup("credential");
  render(<BackHarness props={fixture.props} />);
  await clickPasskey();
  fireEvent.click(screen.getByRole("button", { name: "Back to email" }));
  assert.ok(screen.getByRole("button", { name: /Sign(?:ing)? in/i }));
  assert.equal(fixture.seen.credentialOptions?.signal?.aborted, true);
  fixture.credential.resolve(new SyntheticCredential()); await settle();
  assert.equal(fixture.seen.complete, 0); assert.equal(fixture.seen.success, 0);
});

test("disabled transition retires an active ceremony", async () => {
  const fixture = setup("credential");
  const view = render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey();
  view.rerender(<PasskeyLogin {...fixture.props} disabled />);
  assert.equal(fixture.seen.credentialOptions?.signal?.aborted, true);
  fixture.credential.resolve(new SyntheticCredential()); await settle();
  assert.equal(fixture.seen.complete, 0); assert.equal(fixture.seen.success, 0);
});

test("late native rejection after unmount cannot publish an error", async () => {
  const fixture = setup("credential");
  const view = render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey(); view.unmount();
  fixture.credential.reject(new DOMException("dismissed", "NotAllowedError")); await settle();
  assert.deepEqual(fixture.seen.errors, []);
});

test("StrictMode setup-cleanup-setup still permits an owned login", async () => {
  const fixture = setup("complete");
  render(<StrictMode><PasskeyLogin {...fixture.props} /></StrictMode>);
  await clickPasskey(); fixture.complete.resolve(loginResult); await settle();
  assert.equal(fixture.seen.complete, 1); assert.equal(fixture.seen.success, 1);
});

function retainedClick(button: HTMLElement) {
  const key = Object.keys(button).find(value => value.startsWith("__reactProps$"));
  assert.ok(key, "actual ReactDOM button props are present");
  return (button as unknown as Record<string, { onClick: () => void }>)[key].onClick;
}

test("same-commit double activation admits exactly one ceremony", async () => {
  const fixture = setup("begin");
  render(<PasskeyLogin {...fixture.props} />);
  const click = retainedClick(screen.getByRole("button"));
  await act(async () => { click(); click(); });
  assert.equal(fixture.seen.begin, 1);
  fixture.begin.resolve(startResult); await settle();
  assert.equal(fixture.seen.credential, 1); assert.equal(fixture.seen.complete, 1);
  assert.equal(fixture.seen.success, 1);
});

test("retained click after disable or unmount cannot begin a ceremony", async () => {
  const fixture = setup("begin");
  const view = render(<PasskeyLogin {...fixture.props} />);
  const click = retainedClick(screen.getByRole("button"));
  view.rerender(<PasskeyLogin {...fixture.props} disabled />);
  await act(async () => { click(); });
  assert.equal(fixture.seen.begin, 0);
  view.unmount(); await act(async () => { click(); });
  assert.equal(fixture.seen.begin, 0);
});

test("an abandoned completion cannot clear a replacement attempt's loading state", async () => {
  const fixture = setup("complete");
  const view = render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey(); assert.equal(fixture.seen.complete, 1);
  view.rerender(<PasskeyLogin {...fixture.props} disabled />);
  view.rerender(<PasskeyLogin {...fixture.props} />);
  const replacement = deferred<typeof loginResult>();
  api.completePasskeyLogin = async () => { fixture.seen.complete++; return replacement.promise; };
  await clickPasskey(); assert.equal(fixture.seen.complete, 2);
  fixture.complete.reject(new Error("late old error")); await settle();
  assert.ok(screen.getByRole("button", { name: /Signing in/i }));
  assert.deepEqual(fixture.seen.errors, []);
  replacement.resolve(loginResult); await settle();
  assert.equal(fixture.seen.success, 1);
});

test("an owned native cancellation remains visible and allows a deliberate new attempt", async () => {
  const fixture = setup("credential");
  render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey();
  fixture.credential.reject(new DOMException("cancelled", "NotAllowedError")); await settle();
  assert.match(screen.getByRole("alert").textContent ?? "", /authentication was cancelled/i);
  assert.deepEqual(fixture.seen.errors, ["Passkey authentication was cancelled"]);
  assert.equal(fixture.seen.complete, 0);
  assert.equal((screen.getByRole("button") as HTMLButtonElement).disabled, false);
});

test("completion receives the live operation guard and cancellation signal", async () => {
  const fixture = setup("complete");
  let guard: Parameters<typeof api.completePasskeyLogin>[2];
  let savedCeremony: string | undefined;
  api.completePasskeyLogin = async (ceremony, _assertion, options) => {
    savedCeremony = ceremony; guard = options;
    return fixture.complete.promise;
  };
  const view = render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey();
  assert.equal(savedCeremony, startResult.ceremony_id);
  assert.equal(guard?.ownsOperation?.(), true);
  assert.equal(guard?.signal?.aborted, false);
  view.unmount();
  assert.equal(guard?.ownsOperation?.(), false);
  assert.equal(guard?.signal?.aborted, true);
  fixture.complete.resolve(loginResult); await settle();
  assert.equal(fixture.seen.success, 0);
});

test("identity replacement while begin is pending cannot open a native prompt", async () => {
  const fixture = setup("begin");
  render(<PasskeyLogin {...fixture.props} />);
  await clickPasskey();
  await act(async () => { invalidateRequestIdentity(); });
  fixture.begin.resolve(startResult); await settle();
  assert.equal(fixture.seen.credential, 0); assert.equal(fixture.seen.complete, 0);
  assert.equal(fixture.seen.success, 0);
});

test("Back while begin is pending cannot open a prompt in the exiting view", async () => {
  const fixture = setup("begin");
  render(<BackHarness props={fixture.props} />);
  await clickPasskey();
  fireEvent.click(screen.getByRole("button", { name: "Back to email" }));
  assert.ok(screen.getByRole("button", { name: /Sign(?:ing)? in/i }));
  fixture.begin.resolve(startResult); await settle();
  assert.equal(fixture.seen.credential, 0); assert.equal(fixture.seen.complete, 0);
});

test("Back during completion cannot adopt a result in the exiting view", async () => {
  const fixture = setup("complete");
  render(<BackHarness props={fixture.props} />);
  await clickPasskey(); assert.equal(fixture.seen.complete, 1);
  fireEvent.click(screen.getByRole("button", { name: "Back to email" }));
  assert.ok(screen.getByRole("button", { name: /Sign(?:ing)? in/i }));
  fixture.complete.resolve(loginResult); await settle();
  assert.equal(fixture.seen.success, 0); assert.deepEqual(fixture.seen.errors, []);
});

test("real API/HTTP completion checks the abandoned ceremony again after queue admission", async () => {
  const fixture = setup("credential");
  api.beginPasskeyLogin = originalBegin;
  api.completePasskeyLogin = originalComplete;
  const grant = deferred<void>();
  let queued = 0;
  let sentCompletions = 0;
  Object.defineProperty(navigator, "locks", { configurable: true, value: {
    request: async (_name: string, _options: unknown, callback: () => Promise<unknown>) => {
      queued++;
      // Deliberately still grant after abort: the production transport must
      // consult the component owner at grant, not rely only on this fixture.
      await grant.promise;
      return callback();
    },
  } });
  globalThis.fetch = async input => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/auth/passkeys/login/begin")) return Response.json(startResult);
    if (path.endsWith("/auth/passkeys/login/complete")) {
      sentCompletions++;
      return Response.json(loginResult);
    }
    throw new Error(`Unexpected test request: ${path}`);
  };
  render(<BackHarness props={fixture.props} />);
  await clickPasskey();
  assert.equal(fixture.seen.credential, 1);
  assert.equal(queued, 0, "the native human prompt never holds the session lock");
  fixture.credential.resolve(new SyntheticCredential()); await settle();
  assert.equal(queued, 1); assert.equal(sentCompletions, 0);
  fireEvent.click(screen.getByRole("button", { name: "Back to email" }));
  grant.resolve(); await settle();
  assert.equal(sentCompletions, 0);
  assert.equal(fixture.seen.success, 0); assert.deepEqual(fixture.seen.errors, []);
});
