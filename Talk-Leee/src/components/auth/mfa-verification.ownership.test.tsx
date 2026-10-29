import React, { StrictMode } from "react";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { api } from "@/lib/api";
import { ApiClientError, invalidateRequestIdentity } from "@/lib/http-client";
import MFAVerification from "./mfa-verification";

const originalVerify = api.verifyMfaChallenge;
const originalFetch = globalThis.fetch;
const originalLocks = Object.getOwnPropertyDescriptor(navigator, "locks");
const response: Awaited<ReturnType<typeof originalVerify>> = {
  access_token: "synthetic-mfa-token", token_type: "bearer", user_id: "user-a", tenant_id: "tenant-a",
  email: "a@example.test", role: "tenant_admin", minutes_remaining: 1, minutes_state: "known", message: "",
  mfa_required: false, mfa_challenge_token: undefined, business_name: undefined,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function setup(strict = false) {
  const pending = deferred<typeof response>();
  const seen = { requests: 0, successes: 0, errors: [] as string[], backs: 0,
    options: undefined as Parameters<typeof originalVerify>[3] };
  api.verifyMfaChallenge = async (_challenge, _code, _recovery, options) => {
    seen.requests++; seen.options = options; return pending.promise;
  };
  const props = { challengeToken: "challenge-a", onSuccess: () => { seen.successes++; },
    onError: (error: string) => { seen.errors.push(error); }, onBackClick: () => { seen.backs++; } };
  const element = <MFAVerification {...props} />;
  const view = render(strict ? <StrictMode>{element}</StrictMode> : element);
  enterCode();
  return { pending, seen, props, view };
}
function enterCode(value = "123456") { fireEvent.change(screen.getByLabelText("Authentication Code"), { target: { value } }); }
function form() { return screen.getByLabelText("Authentication Code").closest("form")!; }
async function submit() { await act(async () => { fireEvent.submit(form()); }); }
async function settle() { await act(async () => { await Promise.resolve(); }); }
function apiError(status?: number, code = "server_error", notSent?: true) {
  return new ApiClientError({ status, code, message: "Synthetic verification refusal", url: "/auth/mfa/verify",
    method: "POST", sessionRequestNotSent: notSent });
}
afterEach(() => {
  cleanup(); api.verifyMfaChallenge = originalVerify; globalThis.fetch = originalFetch;
  if (originalLocks) Object.defineProperty(navigator, "locks", originalLocks); else Reflect.deleteProperty(navigator, "locks");
  invalidateRequestIdentity();
});

test("late MFA result after unmount cannot adopt credentials", async () => {
  const f = setup(); await submit(); f.view.unmount();
  assert.equal(f.seen.options?.signal?.aborted, true);
  assert.equal(f.seen.options?.ownsOperation?.(), false);
  f.pending.resolve(response); await settle();
  assert.equal(f.seen.successes, 0);
});

test("a saved alphanumeric recovery code reaches the existing recovery field unchanged", async () => {
  const f = setup();
  const reviewed: unknown[] = [];
  api.verifyMfaChallenge = async (challenge, code, recovery, options) => {
    reviewed.push([challenge, code, recovery]);
    assert.equal(options?.ownsOperation?.(), true);
    return response;
  };
  fireEvent.click(screen.getByRole("button", { name: "Use a recovery code" }));
  const input = screen.getByLabelText("Recovery Code") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "AbCd_EfG-hIjK-LmN0" } });
  assert.equal(input.value, "AbCd_EfG-hIjK-LmN0");
  assert.equal(input.inputMode, "text");
  assert.equal(screen.queryByText(/Expires in/), null);
  await act(async () => { fireEvent.submit(input.closest("form")!); });
  assert.deepEqual(reviewed, [["challenge-a", undefined, "AbCd_EfG-hIjK-LmN0"]]);
  assert.equal(f.seen.successes, 1);
});

test("recovery verification retains ownership, duplicate and unknown-outcome safeguards", async () => {
  const f = setup();
  fireEvent.click(screen.getByRole("button", { name: "Use a recovery code" }));
  const input = screen.getByLabelText("Recovery Code");
  fireEvent.change(input, { target: { value: "AbCdEfGh-IjKlMnOp" } });
  await act(async () => { fireEvent.submit(input.closest("form")!); fireEvent.submit(input.closest("form")!); });
  assert.equal(f.seen.requests, 1);
  assert.equal((screen.getByRole("button", { name: "Use an authenticator code" }) as HTMLButtonElement).disabled, true);
  f.pending.reject(apiError(503)); await settle();
  assert.match(screen.getByRole("alert").textContent ?? "", /could not confirm whether verification completed/i);
  assert.equal((screen.getByRole("button", { name: "Verify" }) as HTMLButtonElement).disabled, true);
  fireEvent.submit(input.closest("form")!);
  assert.equal(f.seen.requests, 1);
  f.view.rerender(<MFAVerification {...f.props} challengeToken="challenge-b" />);
  assert.equal((screen.getByLabelText("Authentication Code") as HTMLInputElement).value, "");
});

test("switching between recovery and authenticator discards the previous secret", () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Use a recovery code" }));
  assert.equal((screen.getByLabelText("Recovery Code") as HTMLInputElement).value, "");
  fireEvent.change(screen.getByLabelText("Recovery Code"), { target: { value: "AbCdEfGh-IjKlMnOp" } });
  fireEvent.click(screen.getByRole("button", { name: "Use an authenticator code" }));
  assert.equal((screen.getByLabelText("Authentication Code") as HTMLInputElement).value, "");
  assert.equal((screen.getByRole("button", { name: "Verify" }) as HTMLButtonElement).disabled, true);
});

test("challenge replacement rejects old result and admits only the newly entered code", async () => {
  const f = setup(); await submit();
  f.view.rerender(<MFAVerification {...f.props} challengeToken="challenge-b" />);
  assert.equal(f.seen.options?.signal?.aborted, true);
  assert.equal((screen.getByLabelText("Authentication Code") as HTMLInputElement).value, "");
  f.pending.resolve(response); await settle(); assert.equal(f.seen.successes, 0);
  const reviewed: unknown[] = [];
  api.verifyMfaChallenge = async (challenge, code) => { reviewed.push([challenge, code]); return response; };
  enterCode("654321"); await submit();
  assert.deepEqual(reviewed, [["challenge-b", "654321"]]); assert.equal(f.seen.successes, 1);
});

test("identity replacement retires the old MFA challenge and late response", async () => {
  const f = setup(); await submit();
  await act(async () => { invalidateRequestIdentity(); });
  assert.equal(f.seen.options?.signal?.aborted, true);
  f.pending.resolve(response); await settle();
  assert.equal(f.seen.successes, 0);
  assert.match(screen.getByRole("alert").textContent ?? "", /sign-in changed/i);
  await submit(); assert.equal(f.seen.requests, 1);
});

test("late MFA error after unmount cannot publish into parent state", async () => {
  const f = setup(); await submit(); f.view.unmount();
  f.pending.reject(new Error("late error")); await settle();
  assert.deepEqual(f.seen.errors, []);
});

test("same-commit duplicate submissions admit one request; normal pending Back stays disabled", async () => {
  const f = setup();
  await act(async () => { fireEvent.submit(form()); fireEvent.submit(form()); });
  assert.equal(f.seen.requests, 1);
  const back = screen.getByRole("button", { name: "Back to login" });
  assert.equal((back as HTMLButtonElement).disabled, true);
  fireEvent.click(back); assert.equal(f.seen.backs, 0);
  f.pending.resolve(response); await settle();
  assert.equal(f.seen.successes, 1);
  await submit(); assert.equal(f.seen.requests, 1, "completed challenge cannot be resubmitted");
});

test("StrictMode permits a current MFA response and its intentional identity adoption", async () => {
  const f = setup(true);
  f.view.rerender(<StrictMode><MFAVerification {...f.props} onSuccess={() => {
    f.seen.successes++; invalidateRequestIdentity();
  }} /></StrictMode>);
  await submit(); f.pending.resolve(response); await settle();
  assert.equal(f.seen.successes, 1); assert.equal(screen.queryByRole("alert"), null);
});

for (const status of [401, 429]) test(`explicit ${status} refusal permits a deliberate new code attempt`, async () => {
  const f = setup(); let calls = 0;
  api.verifyMfaChallenge = async () => { calls++; if (calls === 1) throw apiError(status); return response; };
  await submit(); assert.equal(calls, 1); assert.equal(f.seen.successes, 0);
  assert.equal((screen.getByRole("button", { name: "Verify" }) as HTMLButtonElement).disabled, false);
  enterCode("654321"); await submit();
  assert.equal(calls, 2); assert.equal(f.seen.successes, 1);
});

for (const mode of ["network", "503", "408", "invalid_response", "blank-token"] as const) {
  test(`unknown ${mode} outcome blocks replay of the same MFA challenge`, async () => {
    const f = setup(); let calls = 0;
    api.verifyMfaChallenge = async () => {
      calls++;
      if (mode === "blank-token") return { ...response, access_token: " " };
      throw apiError(mode === "503" ? 503 : mode === "408" ? 408 : undefined, mode);
    };
    await submit(); assert.equal(calls, 1); assert.equal(f.seen.successes, 0);
    assert.match(screen.getByRole("alert").textContent ?? "", /could not confirm whether verification completed/i);
    await submit(); assert.equal(calls, 1);
    assert.equal((screen.getByRole("button", { name: "Verify" }) as HTMLButtonElement).disabled, true);
    fireEvent.click(screen.getByRole("button", { name: "Back to login" }));
    assert.equal(f.seen.backs, 1);
  });
}

test("local proven-unsent failure preserves challenge and permits deliberate retry", async () => {
  const f = setup(); let calls = 0;
  api.verifyMfaChallenge = async () => { calls++; if (calls === 1) throw apiError(undefined, "SESSION_COORDINATION_TIMEOUT", true); return response; };
  await submit(); assert.equal(calls, 1);
  assert.equal((screen.getByLabelText("Authentication Code") as HTMLInputElement).value, "123456");
  await submit(); assert.equal(calls, 2); assert.equal(f.seen.successes, 1);
});

test("actual API/HTTP queue rechecks unmounted MFA owner before sending", async () => {
  const f = setup(); api.verifyMfaChallenge = originalVerify;
  const grant = deferred<void>(); let queued = 0; let fetches = 0;
  Object.defineProperty(navigator, "locks", { configurable: true, value: {
    request: async (_name: string, _options: unknown, run: () => Promise<unknown>) => {
      queued++; await grant.promise; return run();
    },
  } });
  globalThis.fetch = async () => { fetches++; return Response.json(response); };
  await submit(); assert.equal(queued, 1); assert.equal(fetches, 0);
  f.view.unmount(); grant.resolve(); await settle();
  assert.equal(fetches, 0); assert.equal(f.seen.successes, 0);
});

test("HTTP body cannot forge the local unsent marker to replay an unknown MFA outcome", async () => {
  const f = setup(); api.verifyMfaChallenge = originalVerify; let fetches = 0;
  globalThis.fetch = async () => { fetches++; return Response.json({ error: {
    code: "SESSION_COORDINATION_UNAVAILABLE", message: "Not sent", sessionRequestNotSent: true,
  } }, { status: 503 }); };
  await submit(); assert.equal(fetches, 1);
  assert.match(screen.getByRole("alert").textContent ?? "", /could not confirm/i);
  await submit(); assert.equal(fetches, 1); assert.equal(f.seen.successes, 0);
});
