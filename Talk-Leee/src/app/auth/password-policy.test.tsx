import React from "react";
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext, type AppRouterInstance } from "next/dist/shared/lib/app-router-context.shared-runtime";
import { SearchParamsContext } from "next/dist/shared/lib/hooks-client-context.shared-runtime";
import RegisterPage from "./register/register-client";
import ForgotPasswordPage from "./forgot-password/page";
import { AuthProvider } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { ApiClientError, clearFreshLoginGrace } from "@/lib/http-client";

// Keep the real SecurityPage and password form. Only its unrelated dashboard
// shell and device/MFA/passkey widgets are replaced, as in page.contract.test.tsx.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main> };
    }
    if (/components\/auth\/(mfa-setup|passkey-registration|passkey-list|device-list)$/.test(id)) {
        return { __esModule: true, default: () => null };
    }
    return originalLoad.call(this, id, ...args);
};
let SecurityPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- scoped unrelated-widget stubs above
    SecurityPage = require("../security/page").default;
} finally {
    loader._load = originalLoad;
}

const router = { back() {}, forward() {}, refresh() {}, push() {}, replace() {}, prefetch() {}, bfcacheId: "test" } satisfies AppRouterInstance;
const email = "person@example.test";
const code = "123456";
const password15 = "Rivet!8-fog#27x";
const password14 = password15.slice(0, 14);
const legacyCurrentPassword = "legacy-8";
const commonPasswordError = "Choose a password that is not commonly used.";
const anonymousSession = new ApiClientError({ status: 401, code: "unauthorized", message: "No browser session.", url: "/auth/me", method: "GET" });
const originalBearerFallback = process.env.NEXT_PUBLIC_BEARER_FALLBACK;

beforeEach(() => {
    localStorage.clear();
    clearFreshLoginGrace();
    process.env.NEXT_PUBLIC_BEARER_FALLBACK = "true";
    window.history.replaceState({}, "", "/auth/register");
    mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected network request in password policy test"); });
    mock.method(api, "getMe", async () => { throw anonymousSession; });
    mock.method(api, "getMfaStatus", async () => ({ enabled: false, verified_at: null, recovery_codes_remaining: 0 }));
});

afterEach(() => {
    cleanup();
    mock.restoreAll();
    localStorage.clear();
    clearFreshLoginGrace();
    if (originalBearerFallback === undefined) delete process.env.NEXT_PUBLIC_BEARER_FALLBACK;
    else process.env.NEXT_PUBLIC_BEARER_FALLBACK = originalBearerFallback;
});

function draw(page: React.ReactNode, query = "") {
    return render(<AppRouterContext.Provider value={router}>
        <SearchParamsContext.Provider value={new URLSearchParams(query)}>
            <AuthProvider>{page}</AuthProvider>
        </SearchParamsContext.Provider>
    </AppRouterContext.Provider>);
}

function input(label: string, value: string) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

type Flow = {
    name: string;
    passwordLabel: string;
    confirmationLabel: string;
    button: string;
    open: (calls: unknown[][]) => Promise<void>;
    expectedCall: (password: string) => unknown[];
};

const flows: Flow[] = [
    {
        name: "registration",
        passwordLabel: "Password",
        confirmationLabel: "Confirm Password",
        button: "Create Account",
        async open(calls) {
            mock.method(api, "signupStart", async () => ({ email, message: "Verification code sent!", expires_in_minutes: 10 }));
            mock.method(api, "signupVerifyCode", async () => ({ email, message: "Verified" }));
            mock.method(api, "signupComplete", async (...args: unknown[]) => {
                calls.push(args);
                throw new Error(commonPasswordError);
            });
            draw(<RegisterPage />);
            input("Your Name", "Person");
            input("Business Name", "Example Company");
            input("Work Email", email);
            fireEvent.click(screen.getByRole("button", { name: "Get Started" }));
            await screen.findByLabelText("Verification Code");
            input("Verification Code", code);
            fireEvent.click(screen.getByRole("button", { name: "Next" }));
            await screen.findByLabelText("Password");
        },
        expectedCall: password => [email, code, password, password],
    },
    {
        name: "password reset",
        passwordLabel: "New password",
        confirmationLabel: "Confirm new password",
        button: "Reset Password",
        async open(calls) {
            mock.method(api, "request", async (options: { path: string; method: string; body: unknown }) => {
                if (options.path === "/auth/forgot-password") return { message: "Reset code requested." };
                assert.equal(options.path, "/auth/reset-password");
                calls.push([options]);
                throw new Error(commonPasswordError);
            });
            draw(<ForgotPasswordPage />, `email=${encodeURIComponent(email)}`);
            fireEvent.click(screen.getByRole("button", { name: "Send Reset Code" }));
            await screen.findByLabelText("Code");
            input("Code", code);
        },
        expectedCall: password => [{ path: "/auth/reset-password", method: "POST", body: { email, code, new_password: password, confirm_password: password } }],
    },
    {
        name: "password change",
        passwordLabel: "New password",
        confirmationLabel: "Confirm new password",
        button: "Change password",
        async open(calls) {
            mock.method(api, "changePassword", async (...args: unknown[]) => {
                calls.push(args);
                throw new Error(commonPasswordError);
            });
            draw(<SecurityPage />);
            await screen.findByLabelText("Current password");
            input("Current password", legacyCurrentPassword);
        },
        expectedCall: password => [legacyCurrentPassword, password],
    },
];

for (const flow of flows) {
    test(`${flow.name} advertises and applies the 15-character minimum`, async () => {
        await flow.open([]);
        const password = screen.getByLabelText(flow.passwordLabel) as HTMLInputElement;
        const confirmation = screen.getByLabelText(flow.confirmationLabel) as HTMLInputElement;
        assert.equal(password.minLength, 15);
        assert.equal(confirmation.minLength, 15);
        assert.equal(password.placeholder, "At least 15 characters");
        if (flow.name === "password change") {
            assert.equal((screen.getByLabelText("Current password") as HTMLInputElement).minLength, -1);
        }
    });

    test(`${flow.name} rejects 14 characters before the credential API even on direct form submission`, async () => {
        const calls: unknown[][] = [];
        await flow.open(calls);
        input(flow.passwordLabel, password14);
        input(flow.confirmationLabel, password14);
        const form = screen.getByRole("button", { name: flow.button }).closest("form");
        assert.ok(form);
        // Direct submission avoids depending on jsdom's native length validation.
        await act(async () => { fireEvent.submit(form); });
        assert.equal(calls.length, 0);
        assert.match(screen.getByRole("alert").textContent ?? "", /at least 15 characters/i);
    });

    test(`${flow.name} sends exactly 15 characters and displays the backend common-password rejection`, async () => {
        const calls: unknown[][] = [];
        await flow.open(calls);
        assert.equal(password15.length, 15);
        input(flow.passwordLabel, password15);
        input(flow.confirmationLabel, password15);
        const button = screen.getByRole("button", { name: flow.button }) as HTMLButtonElement;
        assert.equal(button.disabled, false);
        fireEvent.click(button);
        assert.equal((await screen.findByRole("alert")).textContent, commonPasswordError);
        assert.deepEqual(calls, [flow.expectedCall(password15)]);
    });
}
