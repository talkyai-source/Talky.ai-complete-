/**
 * FX6c Task 6 (plan 3.7: "'Today', daily charts and date filters must use the
 * customer's configured timezone."). Settings → Account & Security has the
 * account's Timezone directly under Business name: a tenant admin or above
 * picks from the full zone list and it is saved by the form's own button,
 * only when it changed (PUT /auth/account/timezone); everyone else sees it
 * read-only. A 403 or 422 shows the server's own message on the field.
 * Same boundary stubs as page.forms.test.tsx.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "@/lib/api";
import { supportedTimeZones } from "@/lib/contact-form";
import { ApiClientError } from "@/lib/http-client";

let role = "tenant_admin";
let accountTimezone: string | undefined = "Europe/London";
let refreshes = 0;
const toasts: Array<{ type: string; title: string; message?: string }> = [];

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
const refreshUser = async () => { refreshes++; };
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/auth/device-list")) return { __esModule: true, default: nothing };
    if (id.includes("components/settings/telephony-providers-section")) return { TelephonyProvidersSection: nothing };
    if (id.includes("components/settings/recording-policy-section")) return { RecordingPolicySection: nothing };
    if (id.endsWith("lib/auth-context")) {
        return { useAuth: () => ({ user: { id: "u", email: "a@example.test", role, name: "Ada", business_name: "Acme", account_timezone: accountTimezone }, loading: false, refreshUser }) };
    }
    if (id.endsWith("lib/auth-hooks")) return { useAccessToken: () => "" };
    if (id.endsWith("lib/notifications-client")) {
        const real = originalLoad.call(this, id, ...args) as {
            useNotificationsState: () => Record<string, unknown>;
            useNotificationsActions: () => Record<string, unknown>;
        };
        return {
            ...real,
            useNotificationsState: () => ({ ...real.useNotificationsState(), hydrated: true }),
            useNotificationsActions: () => ({ ...real.useNotificationsActions(), create: (toast: { type: string; title: string; message?: string }) => { toasts.push(toast); } }),
        };
    }
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(() => { cleanup(); mock.restoreAll(); role = "tenant_admin"; accountTimezone = "Europe/London"; refreshes = 0; toasts.length = 0; });

const picker = () => screen.getByRole("combobox", { name: "Timezone" }) as HTMLButtonElement;
const saveButton = () => screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement;
async function pick(zone: string) {
    await act(async () => { fireEvent.click(picker()); });
    const option = (await screen.findAllByRole("option")).find((node) => node.textContent === zone);
    assert.equal(option !== undefined, true, `${zone} is offered`);
    await act(async () => { fireEvent.click(option!); });
}
/** Mocks both save requests; returns what was sent. */
function wire(timezoneAnswer: () => Promise<unknown> = async () => ({ reporting_timezone: "Asia/Karachi", account_timezone: "Asia/Karachi" })) {
    const sent = { timezone: [] as string[], profile: [] as unknown[] };
    mock.method(api, "updateAccountTimezone", async (zone: string) => { sent.timezone.push(zone); return timezoneAnswer(); });
    mock.method(api, "updateMe", async (payload: unknown) => { sent.profile.push(payload); return {}; });
    return sent;
}
const refusal = (status: number, code: string, message: string) =>
    new ApiClientError({ status, code, message, url: "/auth/account/timezone", method: "PUT" });

test("the Timezone field sits directly under Business name, with the existing label, and shows the account's zone", () => {
    render(<Page />);
    const business = screen.getByLabelText("Business name");
    const field = business.parentElement!.nextElementSibling as HTMLElement;
    assert.equal(field.querySelector("label")?.textContent, "Timezone");
    assert.equal(field.contains(picker()), true, "the field under Business name holds the picker");
    assert.equal(picker().textContent, "Europe/London");
    assert.equal(picker().disabled, false);
    assert.equal(saveButton().disabled, true, "nothing changed yet");
});

test("the choices are the full list from supportedTimeZones(), with no clear option", async () => {
    render(<Page />);
    await act(async () => { fireEvent.click(picker()); });
    const offered = (await screen.findAllByRole("option")).map((node) => node.textContent);
    assert.deepEqual(offered, supportedTimeZones());
    assert.equal(offered.length > 300, true, "the full list, not the short calling-hours list");
});

test("no zone on the account reads the existing \"Not set\"", () => {
    accountTimezone = undefined;
    render(<Page />);
    assert.equal(picker().textContent, "Not set");
    assert.equal(saveButton().disabled, true);
});

for (const adminRole of ["tenant_admin", "partner_admin", "platform_admin", "admin", "owner", "super_admin"]) {
    test(`${adminRole} (passes the server's tenant-admin check) can change the zone`, () => {
        role = adminRole;
        render(<Page />);
        assert.equal(picker().disabled, false);
    });
}

for (const otherRole of ["campaign_manager", "user", "agent", "billing_user", "readonly", "operator", "white_label_admin", "member"]) {
    test(`${otherRole} (refused by the server) sees the zone read-only, the way Business name is`, () => {
        role = otherRole;
        render(<Page />);
        assert.equal(screen.queryByRole("combobox", { name: "Timezone" }) === null, true, "no picker");
        const field = screen.getByLabelText("Timezone") as HTMLInputElement;
        assert.equal(field.value, "Europe/London");
        assert.equal(field.readOnly, true);
        assert.equal(field.disabled, true);
        const business = screen.getByLabelText("Business name") as HTMLInputElement;
        assert.equal(business.readOnly && business.disabled, true);
    });
}

test("a non-admin with no zone on the account reads \"Not set\"", () => {
    role = "user";
    accountTimezone = undefined;
    render(<Page />);
    assert.equal((screen.getByLabelText("Timezone") as HTMLInputElement).value, "Not set");
});

test("a changed zone is saved by the form's own button, and the profile is read again", async () => {
    const sent = wire();
    render(<Page />);
    await pick("Asia/Karachi");
    assert.equal(picker().textContent, "Asia/Karachi");
    assert.equal(saveButton().disabled, false);
    await act(async () => { fireEvent.click(saveButton()); });
    await waitFor(() => assert.equal(refreshes, 1));
    assert.deepEqual(sent.timezone, ["Asia/Karachi"]);
    assert.deepEqual(sent.profile, [], "the profile request is not sent when only the zone changed");
    assert.deepEqual(toasts, [{ type: "success", title: "Profile updated", message: "Your changes have been saved." }]);
    assert.equal(screen.queryByRole("alert") === null, true);
});

test("an unchanged zone is not sent when another field is saved", async () => {
    const sent = wire();
    render(<Page />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ada L" } });
    await act(async () => { fireEvent.click(saveButton()); });
    await waitFor(() => assert.equal(refreshes, 1));
    assert.deepEqual(sent.profile, [{ name: "Ada L" }]);
    assert.deepEqual(sent.timezone, [], "the zone did not change, so it is not saved");
});

test("picking the zone the account already has leaves the form unchanged", async () => {
    const sent = wire();
    render(<Page />);
    await pick("Asia/Karachi");
    await pick("Europe/London");
    assert.equal(saveButton().disabled, true);
    assert.deepEqual(sent.timezone, []);
});

for (const [status, code, message] of [
    [403, "forbidden", "Only a tenant admin can change the account timezone."],
    [422, "invalid_timezone", "Choose a valid IANA timezone, for example Asia/Karachi."],
] as const) {
    test(`a ${status} shows the server's own message tied to the Timezone field`, async () => {
        wire(async () => { throw refusal(status, code, message); });
        render(<Page />);
        await pick("Asia/Karachi");
        await act(async () => { fireEvent.click(saveButton()); });
        const alert = await screen.findByRole("alert");
        assert.equal(alert.textContent, message);
        assert.equal(picker().getAttribute("aria-invalid"), "true");
        assert.equal(picker().getAttribute("aria-describedby"), alert.id);
        assert.equal(alert.id !== "", true);
        assert.equal(picker().parentElement!.parentElement!.contains(alert), true, "the message is inside the Timezone field");
        assert.deepEqual(toasts, [], "no pop-up: the message belongs to the field");
        assert.equal(refreshes, 0);
        // Choosing again clears the message.
        await pick("Asia/Dubai");
        assert.equal(screen.queryByRole("alert") === null, true);
        assert.equal(picker().hasAttribute("aria-invalid"), false);
    });
}

test("a refused zone does not stop the rest of the form: the name is saved and the message stays on the field", async () => {
    const sent = wire(async () => { throw refusal(403, "forbidden", "Only a tenant admin can change the account timezone."); });
    render(<Page />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ada L" } });
    await pick("Asia/Karachi");
    await act(async () => { fireEvent.click(saveButton()); });
    await waitFor(() => assert.equal(refreshes, 1));
    assert.deepEqual(sent.profile, [{ name: "Ada L" }]);
    assert.equal((await screen.findByRole("alert")).textContent, "Only a tenant admin can change the account timezone.");
    assert.deepEqual(toasts.map((toast) => toast.title), ["Profile updated"]);
});

test("any other failed zone save behaves as the form does today (the Save failed pop-up)", async () => {
    wire(async () => { throw refusal(500, "server_error", "Internal error"); });
    render(<Page />);
    await pick("Asia/Karachi");
    await act(async () => { fireEvent.click(saveButton()); });
    await waitFor(() => assert.deepEqual(toasts, [{ type: "error", title: "Save failed", message: "Internal error" }]));
    assert.equal(screen.queryByRole("alert") === null, true);
});
