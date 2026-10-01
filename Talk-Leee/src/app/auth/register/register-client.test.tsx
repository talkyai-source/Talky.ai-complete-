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
const login = api.login;
const getMe = api.getMe;
afterEach(() => { cleanup(); api.signupStart = start; api.login = login; api.getMe = getMe; localStorage.clear(); });
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
    fireEvent.click(screen.getByRole("button", { name: "Get Started" }));
    assert.equal((await screen.findByRole("alert")).textContent, message);
    assert.equal((screen.getByLabelText("Work Email") as HTMLInputElement).disabled, false);
    assert.equal(screen.getByRole("link", { name: "Sign in" }).getAttribute("href"), "/auth/login");
    assert.equal(screen.queryByLabelText(/verification code/i), null);
});

test("wrong login password remains an invalid-credentials error, not duplicate signup", async () => {
    api.login = async () => { throw new ApiClientError({ status: 401, code: "invalid_credentials", message: "Invalid email or password", url: "/auth/login", method: "POST" }); };
    mount(<LoginClientPage />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue with Password" }));
    const password = await screen.findByLabelText("Password");
    fireEvent.change(password, { target: { value: "wrong-password" } });
    fireEvent.submit(password.closest("form")!);
    assert.equal((await screen.findByRole("alert")).textContent, "Invalid email or password");
    assert.equal(screen.queryByText(/You are already registered/), null);
});
