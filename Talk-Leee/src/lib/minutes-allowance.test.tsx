import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { useLayoutEffect } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { api, LoginResponseSchema, MeResponseSchema } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { minutesSummaryPresentation, remainingMinutesLabel } from "@/lib/minutes-allowance";

const login = { access_token: "synthetic", user_id: "user", email: "test@example.com", role: "tenant_admin" };
const originalGetMe = api.getMe;
afterEach(() => { cleanup(); api.getMe = originalGetMe; localStorage.clear(); });

for (const state of ["known", "unlimited", "unavailable"] as const) {
  test(`login and profile preserve ${state} allowance without inventing a balance`, () => {
    const expected = state === "known" ? 17 : null;
    const result = LoginResponseSchema.parse({ ...login, minutes_state: state, minutes_remaining: expected });
    const profile = MeResponseSchema.parse({ id: "user", email: login.email, role: login.role, minutes_state: state, minutes_remaining: expected });
    assert.equal(result.minutes_remaining, expected);
    assert.equal(profile.minutes_remaining, expected);
    assert.equal(profile.minutes_state, state);
  });
}

test("an older response with a number but no state is explicitly unavailable", () => {
  const result = LoginResponseSchema.parse({ ...login, minutes_remaining: 5000 });
  assert.equal(result.minutes_state, "unavailable");
  assert.equal(result.minutes_remaining, null);
});

test("dashboard displays actual overage without clipping settled usage to allowance", () => {
  const result = minutesSummaryPresentation({ minutes_state: "known", minutes_used: 125, minutes_included: 100, minutes_remaining: 0 });
  assert.equal(result.usedText, "125");
  assert.equal(result.percentText, "125% used");
  assert.equal(result.barPercent, 100);
});

test("dashboard distinguishes missing, unavailable and unlimited data", () => {
  for (const value of [undefined, { minutes_state: "unavailable" as const, minutes_used: null, minutes_remaining: null }]) {
    const result = minutesSummaryPresentation(value);
    assert.equal(result.usedText, "Unavailable");
    assert.equal(result.remainingText, "Unavailable");
    assert.equal(result.totalText, "Unavailable");
    assert.doesNotMatch(result.percentText, /0%/);
  }
  const result = minutesSummaryPresentation({ minutes_state: "unlimited", minutes_used: 125, minutes_included: 0, minutes_remaining: null });
  assert.equal(result.remainingText, "Unlimited");
  assert.equal(result.usedText, "125");
  assert.equal(result.totalText, "Unlimited");
});

test("actual auth context keeps authentication while rendering unknown then unlimited allowance", async () => {
  api.getMe = async () => { throw new Error("synthetic anonymous initial load"); };
  let auth!: ReturnType<typeof useAuth>;
  function Consumer() {
    const value = useAuth();
    useLayoutEffect(() => { auth = value; });
    return <p>{value.user ? `${value.status}: ${remainingMinutesLabel(value.user)}` : value.status}</p>;
  }
  render(<AuthProvider><Consumer /></AuthProvider>);
  await waitFor(() => assert.equal(auth.loading, false));
  act(() => auth.applyLoginResult({ ...login, minutes_state: "unavailable", minutes_remaining: null }));
  assert.ok(screen.getByText("authenticated: Unavailable"));
  act(() => auth.applyLoginResult({ ...login, minutes_state: "unlimited", minutes_remaining: null }));
  assert.ok(screen.getByText("authenticated: Unlimited"));
});
