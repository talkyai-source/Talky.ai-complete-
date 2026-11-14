/** FX1b Task 7 — the 2FA step accepts the recovery code it offers and sends it as `recovery_code`. */
import React from "react";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, sharedHttpClient } from "@/lib/api";
import { invalidateRequestIdentity } from "@/lib/http-client";
import MFAVerification from "./mfa-verification";

const originalVerify = api.verifyMfaChallenge;
const client = sharedHttpClient();
const originalRequest = client.request;
const response: Awaited<ReturnType<typeof originalVerify>> = {
  access_token: "synthetic-mfa-token", token_type: "bearer", user_id: "user-a", tenant_id: "tenant-a",
  email: "a@example.test", role: "tenant_admin", minutes_remaining: 1, minutes_state: "known", message: "",
  mfa_required: false, mfa_challenge_token: undefined, business_name: undefined,
};
const RECOVERY_CODE = "AbCdEfGh-IjKlMnOp";

afterEach(() => {
  cleanup();
  api.verifyMfaChallenge = originalVerify;
  client.request = originalRequest;
  invalidateRequestIdentity();
});

function field() { return document.getElementById("totp") as HTMLInputElement; }
/** FX1c Task 3: the switch text of the Admin sign-in (Admin/frontend/src/pages/LoginPage.tsx:47). */
function switchToRecoveryCode() { fireEvent.click(screen.getByRole("button", { name: "Use a recovery code" })); }
function mount() {
  const seen = { successes: 0, errors: [] as string[] };
  render(<MFAVerification challengeToken="challenge-a" onSuccess={() => { seen.successes++; }}
    onError={(error) => { seen.errors.push(error); }} onBackClick={() => {}} />);
  return seen;
}
async function submit() {
  await act(async () => { fireEvent.submit(field().closest("form")!); });
}

test("the field keeps the letters of a recovery code and Verify is enabled", () => {
  mount();
  switchToRecoveryCode();
  fireEvent.change(field(), { target: { value: RECOVERY_CODE } });
  assert.equal(field().value, RECOVERY_CODE);
  assert.equal(field().checkValidity(), true);
  assert.equal((screen.getByRole("button", { name: /Verify/ }) as HTMLButtonElement).disabled, false);
});

test("a recovery code is sent as recovery_code, with no code", async () => {
  const calls: Array<[string, string | undefined, string | undefined]> = [];
  api.verifyMfaChallenge = async (challenge, code, recovery) => { calls.push([challenge, code, recovery]); return response; };
  const seen = mount();
  switchToRecoveryCode();
  fireEvent.change(field(), { target: { value: RECOVERY_CODE } });
  await submit();
  await waitFor(() => assert.equal(seen.successes, 1));
  assert.deepEqual(calls, [["challenge-a", undefined, RECOVERY_CODE]]);
  assert.deepEqual(seen.errors, []);
});

test("the request body carries recovery_code for a recovery code", async () => {
  const bodies: unknown[] = [];
  client.request = (async (request: { path: string; body?: unknown }) => {
    bodies.push({ path: request.path, body: request.body });
    return response;
  }) as typeof client.request;
  const seen = mount();
  switchToRecoveryCode();
  fireEvent.change(field(), { target: { value: RECOVERY_CODE } });
  await submit();
  await waitFor(() => assert.equal(seen.successes, 1));
  assert.deepEqual(bodies, [{ path: "/auth/mfa/verify", body: { challenge_token: "challenge-a", recovery_code: RECOVERY_CODE } }]);
});

test("a 6-digit code keeps working and is sent as code", async () => {
  const calls: Array<[string, string | undefined, string | undefined]> = [];
  api.verifyMfaChallenge = async (challenge, code, recovery) => { calls.push([challenge, code, recovery]); return response; };
  const seen = mount();
  fireEvent.change(field(), { target: { value: "123 456" } });
  assert.equal(field().value, "123456");
  await submit();
  await waitFor(() => assert.equal(seen.successes, 1));
  assert.deepEqual(calls, [["challenge-a", "123456", undefined]]);
});

// ── FX1c Task 3 ─────────────────────────────────────────────────────────────

test("the authenticator field keeps the numeric keypad and 6-8 digits", () => {
  mount();
  assert.equal(screen.getByLabelText("Authentication Code") === field(), true);
  assert.equal(field().getAttribute("inputmode"), "numeric");
  assert.equal(field().getAttribute("pattern"), "[0-9]*");
  assert.equal(field().maxLength, 8);
  assert.equal(field().placeholder, "000000");
  fireEvent.change(field(), { target: { value: "12ab34-5678 9" } });
  assert.equal(field().value, "12345678");
});

test("the recovery-code switch uses the Admin texts, accepts letters, and switches back", () => {
  mount();
  fireEvent.change(field(), { target: { value: "123456" } });
  switchToRecoveryCode();
  assert.equal(screen.getByLabelText("Recovery code") === field(), true);
  assert.equal(field().placeholder, "Enter the complete recovery code");
  assert.equal(field().getAttribute("inputmode"), "text");
  assert.equal(field().hasAttribute("pattern"), false);
  assert.equal(field().maxLength, 128);
  assert.equal(field().value, "", "switching clears the field");
  assert.equal(screen.queryByText(/Expires in/) !== null, false);
  assert.equal((screen.getByRole("button", { name: /Verify/ }) as HTMLButtonElement).disabled, true);

  fireEvent.click(screen.getByRole("button", { name: "Use authenticator code" }));
  assert.equal(screen.getByLabelText("Authentication Code") === field(), true);
  assert.equal(field().getAttribute("inputmode"), "numeric");
  assert.equal(screen.queryByText(/Expires in/) !== null, true);
});

// ── FX3a Task 1.4: one name for the second step ──────────────────────────────
test("FX3a: with no challenge the error names two-factor authentication, not MFA", async () => {
  const errors: string[] = [];
  render(<MFAVerification challengeToken="" onSuccess={() => {}} onError={(error) => { errors.push(error); }} onBackClick={() => {}} />);
  assert.equal(screen.queryByRole("heading", { name: "Two-factor authentication" }) !== null, true);
  assert.equal(screen.queryByRole("heading", { name: "Two-Factor Authentication" }) !== null, false);
  fireEvent.change(field(), { target: { value: "123456" } });
  await submit();
  await waitFor(() => assert.deepEqual(errors, ["Two-factor authentication session expired. Please sign in again."]));
  assert.equal(screen.queryByText(/MFA session expired/) !== null, false);
});

test("A-F10 the helper line and the error both say 6-8 digits, as the field accepts", () => {
  mount();
  assert.match(screen.getByText(/Open your authenticator app/).textContent ?? "", /6-8 digit code/);
  fireEvent.change(field(), { target: { value: "123" } });
  fireEvent.submit(field().closest("form")!);
  assert.match(screen.getByRole("alert").textContent ?? "", /6-8 digit code/);
});
