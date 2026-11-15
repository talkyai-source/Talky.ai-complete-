/**
 * FX4c Task A2 — on the sign-in, sign-up and reset forms an error is tied to
 * its field: the error has an id, the field names it in aria-describedby and
 * carries aria-invalid="true" while in error. No error text is new.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { PathnameContext, SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";

import { LegalAcceptanceField, type LegalAcceptance } from "@/components/auth/legal-acceptance";
import { api } from "@/lib/api";
import { AuthProvider } from "@/lib/auth-context";
import { ApiClientError } from "@/lib/http-client";
import { MIN_PASSWORD_LENGTH } from "@/lib/password-policy";
import ForgotPasswordPage from "./forgot-password/page";
import LoginClientPage from "./login/login-client";
import RegisterClientPage from "./register/register-client";

const SLOW = 15_000;
const original = { start: api.signupStart, verify: api.signupVerifyCode, login: api.login, getMe: api.getMe, request: api.request };
afterEach(() => {
    cleanup();
    api.signupStart = original.start; api.signupVerifyCode = original.verify; api.login = original.login;
    api.getMe = original.getMe; api.request = original.request;
    localStorage.clear();
});
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
function mount(children: React.ReactNode) {
    api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "Sign in", url: "/auth/me", method: "GET" }); };
    render(
        <AppRouterContext.Provider value={router}>
            <PathnameContext.Provider value="/auth/login">
                <SearchParamsContext.Provider value={new URLSearchParams()}>
                    <AuthProvider>{children}</AuthProvider>
                </SearchParamsContext.Provider>
            </PathnameContext.Provider>
        </AppRouterContext.Provider>,
    );
}

const invalid = (field: HTMLElement) => field.getAttribute("aria-invalid") === "true";
const describedBy = (field: HTMLElement) => (field.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean);
/** The field is marked and one of the ids it names holds exactly this text. */
function tiedTo(field: HTMLElement, text: string) {
    return invalid(field) && describedBy(field).some((id) => document.getElementById(id)?.textContent?.trim() === text);
}
/** The field is not marked and names no element holding this text. */
function notTiedTo(field: HTMLElement, text: string) {
    return !invalid(field) && !describedBy(field).some((id) => document.getElementById(id)?.textContent?.trim() === text);
}
const input = (label: string | RegExp) => screen.getByLabelText(label) as HTMLInputElement;

test("sign-in: the refusal is tied to the password field", async () => {
    api.login = async () => { throw new ApiClientError({ status: 401, code: "invalid_credentials", message: "Invalid email or password", url: "/auth/login", method: "POST" }); };
    mount(<LoginClientPage />);
    fireEvent.change(input("Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue with password" }));
    const password = await screen.findByLabelText("Password") as HTMLInputElement;
    assert.equal(notTiedTo(password, "Invalid email or password"), true, "not marked before any error");
    fireEvent.change(password, { target: { value: "wrong-password" } });
    fireEvent.submit(password.closest("form")!);
    await waitFor(() => assert.equal(screen.queryByText("Invalid email or password") !== null, true), { timeout: SLOW });
    assert.equal(tiedTo(input("Password"), "Invalid email or password"), true);
});

test("sign-in: the email check is tied to the email field", async () => {
    mount(<LoginClientPage />);
    fireEvent.change(input("Email"), { target: { value: "not-an-email" } });
    fireEvent.submit(input("Email").closest("form")!);
    await waitFor(() => assert.equal(invalid(input("Email")), true), { timeout: SLOW });
    const ids = describedBy(input("Email"));
    assert.equal(ids.length > 0 && ids.every((id) => document.getElementById(id) !== null), true);
});

test("sign-up: each first-step error is tied to its own field only", async () => {
    let calls = 0;
    api.signupStart = async () => { calls += 1; throw new Error("must not be called"); };
    mount(<RegisterClientPage />);
    fireEvent.change(input("Your Name"), { target: { value: "   " } });
    fireEvent.change(input("Business Name"), { target: { value: "Example" } });
    fireEvent.change(input("Work Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    await waitFor(() => assert.equal(screen.queryByText("Enter your name.") !== null, true), { timeout: SLOW });
    assert.equal(tiedTo(input("Your Name"), "Enter your name."), true);
    assert.equal(notTiedTo(input("Business Name"), "Enter your name."), true);

    fireEvent.change(input("Your Name"), { target: { value: "Example Owner" } });
    fireEvent.change(input("Business Name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    await waitFor(() => assert.equal(screen.queryByText("Enter your business name.") !== null, true), { timeout: SLOW });
    assert.equal(tiedTo(input("Business Name"), "Enter your business name."), true);
    assert.equal(notTiedTo(input("Your Name"), "Enter your business name."), true);
    assert.equal(calls, 0);
});

test("sign-up: 'Passwords do not match.' is tied to the confirm field, not the password field", async () => {
    api.signupStart = (async () => ({ email: "owner@example.com", message: "Verification code sent! Check your email." })) as unknown as typeof api.signupStart;
    api.signupVerifyCode = (async () => ({})) as unknown as typeof api.signupVerifyCode;
    mount(<RegisterClientPage />);
    fireEvent.change(input("Your Name"), { target: { value: "Example Owner" } });
    fireEvent.change(input("Business Name"), { target: { value: "Example" } });
    fireEvent.change(input("Work Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    const code = await screen.findByLabelText(/verification code/i, undefined, { timeout: SLOW }) as HTMLInputElement;
    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.submit(code.closest("form")!);
    const password = await screen.findByLabelText("Password", undefined, { timeout: SLOW }) as HTMLInputElement;
    const confirm = input("Confirm Password");
    assert.equal(invalid(confirm), false, "not marked before anything is typed");

    fireEvent.change(password, { target: { value: "a".repeat(MIN_PASSWORD_LENGTH) } });
    fireEvent.change(confirm, { target: { value: "b".repeat(MIN_PASSWORD_LENGTH) } });
    // The inline line under the field.
    assert.equal(tiedTo(input("Confirm Password"), "Passwords do not match."), true);
    fireEvent.submit(password.closest("form")!);
    await waitFor(() => assert.equal(screen.queryAllByText("Passwords do not match.").length, 2), { timeout: SLOW });
    const named = describedBy(input("Confirm Password")).map((id) => document.getElementById(id)?.textContent?.trim());
    assert.deepEqual(named, ["Passwords do not match.", "Passwords do not match."], "the inline line and the form's error");
    assert.equal(invalid(input("Password")), false, "the password field is not the one in error");

    fireEvent.change(input("Confirm Password"), { target: { value: "a".repeat(MIN_PASSWORD_LENGTH) } });
    assert.equal(screen.queryByText("Passwords do not match.", { selector: "p" }) === null, true, "the inline line is gone once they match");
});

test("sign-up: the terms checkbox is marked and described only while the error is about it", () => {
    const legal = { required: true, unavailable: false, updated: false, accepted: false, setAccepted() {}, blocked: true } as unknown as LegalAcceptance;
    const { rerender } = render(<LegalAcceptanceField legal={legal} />);
    const box = () => screen.getByRole("checkbox") as HTMLInputElement;
    assert.equal(invalid(box()), false);
    assert.equal(box().hasAttribute("aria-describedby"), false);
    rerender(<><p id="form-error">Accept the Terms of Service and Privacy Policy to continue.</p><LegalAcceptanceField legal={legal} errorId="form-error" /></>);
    assert.equal(tiedTo(box(), "Accept the Terms of Service and Privacy Policy to continue."), true);
});

test("reset: the email check has an id the email field names", async () => {
    mount(<ForgotPasswordPage />);
    const email = await screen.findByLabelText(/email/i, undefined, { timeout: SLOW }) as HTMLInputElement;
    fireEvent.change(email, { target: { value: "not-an-email" } });
    fireEvent.submit(email.closest("form")!);
    await waitFor(() => assert.equal(screen.queryByText("Please enter a valid email address") !== null, true), { timeout: SLOW });
    assert.equal(tiedTo(screen.getByLabelText(/email/i) as HTMLInputElement, "Please enter a valid email address"), true);
});

test("reset: each check on the second step is tied to its own field only", async () => {
    api.request = (async () => ({})) as typeof api.request;
    mount(<ForgotPasswordPage />);
    const email = await screen.findByLabelText(/email/i, undefined, { timeout: SLOW }) as HTMLInputElement;
    fireEvent.change(email, { target: { value: "owner@example.com" } });
    fireEvent.submit(email.closest("form")!);
    const code = await screen.findByLabelText(/code/i, undefined, { timeout: SLOW }) as HTMLInputElement;
    const fields = () => ({
        code: document.getElementById("reset-code") as HTMLInputElement,
        password: document.getElementById("reset-new-password") as HTMLInputElement,
        confirm: document.getElementById("reset-confirm-password") as HTMLInputElement,
    });
    const submit = () => fireEvent.submit(code.closest("form")!);
    const tooShort = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;

    fireEvent.change(fields().code, { target: { value: "12" } });
    submit();
    await waitFor(() => assert.equal(screen.queryByText("Enter the 6-digit code from your email.") !== null, true), { timeout: SLOW });
    assert.equal(tiedTo(fields().code, "Enter the 6-digit code from your email."), true);
    assert.equal(invalid(fields().password) || invalid(fields().confirm), false);

    fireEvent.change(fields().code, { target: { value: "123456" } });
    fireEvent.change(fields().password, { target: { value: "short" } });
    submit();
    await waitFor(() => assert.equal(screen.queryByText(tooShort) !== null, true), { timeout: SLOW });
    assert.equal(tiedTo(fields().password, tooShort), true);
    assert.equal(invalid(fields().code) || invalid(fields().confirm), false);

    fireEvent.change(fields().password, { target: { value: "a".repeat(MIN_PASSWORD_LENGTH) } });
    fireEvent.change(fields().confirm, { target: { value: "b".repeat(MIN_PASSWORD_LENGTH) } });
    submit();
    await waitFor(() => assert.equal(screen.queryByText("Passwords do not match.") !== null, true), { timeout: SLOW });
    assert.equal(tiedTo(fields().confirm, "Passwords do not match."), true);
    assert.equal(invalid(fields().code) || invalid(fields().password), false);
});

// FX4c Task A3 — confirm only (the fix is the other developer's, 38398f5c): the
// show-password control is a real button in the Tab order, so Enter and Space
// activate it. The keyboard itself is exercised on the production build.
test("sign-in: the show-password button is a Tab stop and toggles the field", async () => {
    mount(<LoginClientPage />);
    fireEvent.change(input("Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue with password" }));
    const password = await screen.findByLabelText("Password") as HTMLInputElement;
    const toggle = screen.getByRole("button", { name: "Show password" }) as HTMLButtonElement;
    assert.equal(toggle.tagName === "BUTTON" && toggle.type === "button", true);
    assert.equal(toggle.tabIndex, 0);
    assert.equal(toggle.disabled, false);
    assert.equal(password.type, "password");
    fireEvent.click(toggle);
    assert.equal(password.type, "text");
    assert.equal(screen.queryByRole("button", { name: "Hide password" }) !== null, true);
});
