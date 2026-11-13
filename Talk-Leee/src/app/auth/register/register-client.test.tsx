import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { api } from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";
import { AuthProvider } from "@/lib/auth-context";
import RegisterClientPage from "./register-client";
import LoginClientPage from "../login/login-client";

const start = api.signupStart;
const verify = api.signupVerifyCode;
const login = api.login;
const getMe = api.getMe;
afterEach(() => { cleanup(); api.signupStart = start; api.signupVerifyCode = verify; api.login = login; api.getMe = getMe; localStorage.clear(); });
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
function mount(children: React.ReactNode) {
    api.getMe = async () => { throw new ApiClientError({ status: 401, code: "unauthorized", message: "Sign in", url: "/auth/me", method: "GET" }); };
    render(<AppRouterContext.Provider value={router}><AuthProvider>{children}</AuthProvider></AppRouterContext.Provider>);
}

test("duplicate signup shows sign-in guidance and leaves the email editable", async () => {
    const message = "You are already registered. Please sign in or try another email address.";
    api.signupStart = async () => { throw new ApiClientError({ status: 409, code: "email_already_registered", message, url: "/auth/signup/start", method: "POST" }); };
    mount(<RegisterClientPage />);
    fireEvent.change(screen.getByLabelText("Your Name"), { target: { value: "Example Owner" } });
    fireEvent.change(screen.getByLabelText("Business Name"), { target: { value: "Example" } });
    fireEvent.change(screen.getByLabelText("Work Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    assert.equal((await screen.findByRole("alert")).textContent, message);
    assert.equal((screen.getByLabelText("Work Email") as HTMLInputElement).disabled, false);
    assert.equal(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"), "/auth/login");
    assert.equal(screen.queryByLabelText(/verification code/i), null);
});

test("wrong login password remains an invalid-credentials error, not duplicate signup", async () => {
    api.login = async () => { throw new ApiClientError({ status: 401, code: "invalid_credentials", message: "Invalid email or password", url: "/auth/login", method: "POST" }); };
    mount(<LoginClientPage />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue with password" }));
    const password = await screen.findByLabelText("Password");
    fireEvent.change(password, { target: { value: "wrong-password" } });
    fireEvent.submit(password.closest("form")!);
    assert.equal((await screen.findByRole("alert")).textContent, "Invalid email or password");
    assert.equal(screen.queryByText(/You are already registered/), null);
});

test("an unconfirmed logout is explained without claiming server revocation", async () => {
    localStorage.setItem("talky.logout.pending", "synthetic-pending");
    mount(<LoginClientPage />);
    assert.match((await screen.findByRole("status")).textContent ?? "", /could not confirm that the server session ended/);
    assert.match(screen.getByRole("status").textContent ?? "", /revoke other sessions/);
});

test("spaces in the name or business name are refused before anything is sent", async () => {
    let calls = 0;
    api.signupStart = async () => { calls += 1; throw new Error("must not be called"); };
    mount(<RegisterClientPage />);
    fireEvent.change(screen.getByLabelText("Your Name"), { target: { value: "   " } });
    fireEvent.change(screen.getByLabelText("Business Name"), { target: { value: "Example" } });
    fireEvent.change(screen.getByLabelText("Work Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    assert.equal((await screen.findByRole("alert")).textContent, "Enter your name.");
    fireEvent.change(screen.getByLabelText("Your Name"), { target: { value: "Example Owner" } });
    fireEvent.change(screen.getByLabelText("Business Name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    assert.equal((await screen.findByRole("alert")).textContent, "Enter your business name.");
    assert.equal(calls, 0);
});

test("the name and business name fields stop at the 100 characters the server accepts", () => {
    mount(<RegisterClientPage />);
    assert.equal((screen.getByLabelText("Your Name") as HTMLInputElement).maxLength, 100);
    assert.equal((screen.getByLabelText("Business Name") as HTMLInputElement).maxLength, 100);
});

async function reachPasswordStep() {
    api.signupStart = async () => ({ message: "Code sent", expires_in_minutes: 15, email: "owner@example.com" });
    api.signupVerifyCode = async () => ({ message: "ok", email: "owner@example.com" });
    mount(<RegisterClientPage />);
    fireEvent.change(screen.getByLabelText("Your Name"), { target: { value: "Example Owner" } });
    fireEvent.change(screen.getByLabelText("Business Name"), { target: { value: "Example" } });
    fireEvent.change(screen.getByLabelText("Work Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Get started" }));
    fireEvent.change(await screen.findByLabelText("Verification code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByLabelText("Password");
}

test("the password step keeps the length rule on screen and says live when the two passwords differ", async () => {
    await reachPasswordStep();
    const password = screen.getByLabelText("Password");
    assert.match(screen.getByText(/At least 15 characters/).textContent ?? "", /not a common password/);
    assert.ok((password.getAttribute("aria-describedby") ?? "").split(" ").includes(screen.getByText(/At least 15 characters/).id));
    assert.equal(screen.queryByText("Passwords do not match."), null);
    fireEvent.change(password, { target: { value: "a long enough passphrase" } });
    fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "a long enough pass" } });
    assert.ok(screen.getByText("Passwords do not match."));
    fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "a long enough passphrase" } });
    assert.equal(screen.queryByText("Passwords do not match."), null);
});
