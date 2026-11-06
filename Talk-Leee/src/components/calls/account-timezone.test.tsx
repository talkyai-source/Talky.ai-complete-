import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { api, MeResponseSchema, sharedHttpClient } from "@/lib/api";
import { formatTimestamp } from "@/lib/timestamps";
import type { HttpRequestOptions } from "@/lib/http-client";

const AT = "2026-11-01T05:30:00.000Z";
const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const ACCOUNT_ZONE = browserZone === "America/New_York" ? "Asia/Karachi" : "America/New_York";
const NEXT_ZONE = ACCOUNT_ZONE === "America/New_York" ? "Asia/Karachi" : "America/New_York";
const profile = { id: "bbccddee-1234-4abc-8def-0123456789ab", tenant_id: "aabbccdd-1234-4abc-8def-0123456789ab",
    email: "reader@example.test", role: "readonly", account_timezone: ACCOUNT_ZONE };
let currentUser = { ...profile };
let networkReads = 0;
const call = { id: "ccddeeff-1234-4abc-8def-0123456789ab", campaign_id: "ddeeffaa-1234-4abc-8def-0123456789ab",
    campaign_name: "Owned timezone", phone_number: "+14155552671", status: "completed", created_at: AT };
const callsData = { calls: [call, { ...call, id: "eeffaabb-1234-4abc-8def-0123456789ab" }], total: 2 };
const Nothing = () => null;
const permissions = { isSuccess: true, data: { permissions: ["calls:read"] } };

// Render the actual pages and timestamp component. Only unrelated auth/data
// ports and credential widgets are replaced. No per-row settings request exists.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useParams: () => ({ id: call.id }), useRouter: () => ({ push() {}, back() {} }) };
    if (id.includes("components/layout/dashboard-layout")) return {
        DashboardLayout: ({ children }: { children: React.ReactNode }) => <main>{children}</main>,
    };
    if (id.endsWith("lib/auth-context")) return { useAuth: () => ({ user: currentUser, loading: false }) };
    if (id.endsWith("lib/auth-hooks")) return { useAccessToken: () => "synthetic-token" };
    if (id.endsWith("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissions };
    if (id.endsWith("lib/use-call-history-workflow")) return {
        useCallHistoryWorkflow: () => ({ workflow: {}, update() {}, retry() {}, busy: false, failed: false, backupUnavailable: false }),
    };
    if (id.endsWith("lib/api-hooks")) return {
        useCalls: () => ({ data: callsData, isLoading: false, isError: false, refetch: async () => undefined }),
        useCall: () => ({ data: call, isLoading: false, isError: false, refetch: async () => undefined }),
        useCallSummary: () => ({ data: undefined, isLoading: false, isError: false }),
        useCallTranscript: () => ({ data: undefined, isLoading: false, isError: false }),
    };
    if (id.endsWith("lib/mfa-utils")) return {
        getMfaStatus: async () => ({ enabled: true, verifiedAt: AT, recoveryCodesRemaining: 5 }),
        disableMfa: async () => { throw new Error("No credential writes in timezone tests"); },
        regenerateRecoveryCodes: async () => { throw new Error("No credential writes in timezone tests"); },
        validateTotpCode: () => false,
    };
    for (const [path, name] of [
        ["dial-again-button", "DialAgainButton"], ["call-issues-banner", "CallIssuesBanner"],
        ["voice-feedback-recorder", "VoiceFeedbackRecorder"], ["conversation-review-panel", "ConversationReviewPanel"],
        ["lead-details-panel", "LeadDetailsPanel"],
    ]) if (id.endsWith(`components/calls/${path}`)) return { [name]: Nothing };
    if (["mfa-setup", "passkey-registration", "passkey-list", "device-list"].some(name => id.endsWith(`components/auth/${name}`))) {
        return { default: Nothing, __esModule: true };
    }
    return originalLoad.call(this, id, ...args);
};
let CallsPage: React.ComponentType, CallDetailPage: React.ComponentType, SecurityPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- actual pages with scoped boundary ports
    CallsPage = require("../../app/calls/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- actual pages with scoped boundary ports
    CallDetailPage = require("../../app/calls/[id]/page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- actual pages with scoped boundary ports
    SecurityPage = require("../../app/security/page").default;
} finally { loader._load = originalLoad; }

beforeEach(() => {
    currentUser = { ...profile };
    networkReads = 0;
    mock.method(window.HTMLCanvasElement.prototype, "getContext", () => null);
    window.history.replaceState({}, "", "/calls");
    mock.method(globalThis, "fetch", async () => { networkReads += 1; throw new Error("No external or per-row timezone request is allowed"); });
});
afterEach(() => { cleanup(); mock.restoreAll(); });

function offset(zone: string) {
    return new Intl.DateTimeFormat("en", { timeZone: zone, timeZoneName: "shortOffset" })
        .formatToParts(new Date(AT)).find(part => part.type === "timeZoneName")!.value;
}

async function assertTimes(container: HTMLElement, zone: string, minimum: number) {
    await waitFor(() => {
        const times = [...container.querySelectorAll(`time[datetime="${AT}"]`)];
        assert.ok(times.length >= minimum, "The actual loaded page must contain the expected timestamp elements");
        for (const stamp of times) {
            assert.ok(stamp.textContent?.includes(offset(zone)), `Account ${zone} must override browser ${browserZone}: ${stamp.textContent}`);
        }
        const hovered = times.filter(stamp => stamp.hasAttribute("title"));
        assert.ok(hovered.length > 0);
        assert.ok(hovered.every(stamp => stamp.getAttribute("title")?.includes(AT)), "The UTC instant is unchanged");
        assert.equal(networkReads, 0, "Rendering account-zone timestamps must not start any network request");
    });
}

for (const [name, Page, minimum] of [
    ["mobile and desktop call history", CallsPage, 6], ["call detail", CallDetailPage, 1], ["MFA verification", SecurityPage, 1],
] as const) {
    test(`${name} uses the verified account timezone instead of the browser default`, async () => {
        const view = render(<Page />);
        await assertTimes(view.container, ACCOUNT_ZONE, minimum);
    });
}

test("a refreshed account zone updates existing call rows without fetching row settings", async () => {
    const view = render(<CallsPage />);
    await assertTimes(view.container, ACCOUNT_ZONE, 6);
    currentUser = { ...profile, account_timezone: NEXT_ZONE };
    await act(async () => { view.rerender(<CallsPage />); });
    await assertTimes(view.container, NEXT_ZONE, 6);
});

test("the real profile API parser retains account timezone across refresh responses", async () => {
    let reads = 0;
    mock.method(sharedHttpClient(), "request", async (options: HttpRequestOptions) => {
        assert.equal(options.path, "/auth/me");
        reads += 1;
        return { ...profile, account_timezone: reads === 1 ? ACCOUNT_ZONE : NEXT_ZONE };
    });
    assert.equal((await api.getMe()).account_timezone, ACCOUNT_ZONE);
    assert.equal((await api.getMe()).account_timezone, NEXT_ZONE);
    assert.equal(reads, 2);
});

test("older profile responses may omit timezone, but malformed timezone types are refused", () => {
    const { account_timezone: _unused, ...older } = profile;
    assert.equal(MeResponseSchema.parse(older).account_timezone, undefined);
    assert.equal(MeResponseSchema.parse({ ...older, account_timezone: null }).account_timezone ?? null, null);
    assert.throws(() => MeResponseSchema.parse({ ...older, account_timezone: 42 }));
});

test("invalid timestamps are explicit unavailable values and do not throw", () => {
    assert.equal(formatTimestamp("not-a-timestamp", ACCOUNT_ZONE).label, "Unavailable time");
    assert.equal(formatTimestamp("not-a-timestamp", ACCOUNT_ZONE).utc, "Invalid timestamp");
});

test("invalid explicit zones fall back to labelled UTC without changing the instant", () => {
    const result = formatTimestamp(AT, "Not/AZone");
    assert.equal(result.zone, "UTC");
    assert.equal(result.utc, `${AT} (UTC)`);
    assert.ok(result.label.includes("GMT") || result.label.includes("UTC"));
});
