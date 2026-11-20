import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

const user = { id: "user-1", tenant_id: "tenant-1", account_timezone: "Europe/London" };
const call = {
    id: "call-1", status: "completed", direction: "outbound", phone_number: "+447400123456",
    created_at: "2026-07-01T12:00:00Z", duration_seconds: 65,
};
const turns = [{ role: "assistant", content: "Hello there", timestamp: "2026-07-01T12:00:00Z" }];
let grantedPermissions: string[] = ["calls:create"];
const callResult = { data: call, isLoading: false, isError: false, error: null, refetch: () => {} };
const transcriptResult = { data: { turns }, isLoading: false, isError: false, error: null, refetch: () => {} };

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user }) };
    if (id === "next/navigation") return { useParams: () => ({ id: "call-1" }), useRouter: () => ({ push: () => {}, back: () => {} }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/voice-feedback-recorder")) return { VoiceFeedbackRecorder: nothing };
    if (id.includes("components/calls/conversation-review-panel")) return { ConversationReviewPanel: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { LeadDetailsPanel: nothing };
    if (id.endsWith("lib/use-call-history-workflow")) return { useCallHistoryWorkflow: () => ({ workflow: {}, update: () => {}, retry: () => {}, busy: false, failed: false, backupUnavailable: false }) };
    if (id === "@tanstack/react-query") return { ...(originalLoad.call(this, id, ...args) as object), useQuery: () => ({ data: undefined }) };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: true, isError: false, data: { permissions: grantedPermissions } }) };
    if (id.endsWith("lib/api-hooks")) return { useCall: () => callResult, useCallTranscript: () => transcriptResult };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => {};
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
const originalRaf = window.requestAnimationFrame;
const originalCaf = window.cancelAnimationFrame;
beforeEach(() => {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
});
afterEach(() => {
    cleanup();
    window.requestAnimationFrame = originalRaf;
    window.cancelAnimationFrame = originalCaf;
    grantedPermissions = ["calls:create"];
});

test("the call page has the Notes and follow-up card with Edit, and the download button", () => {
    render(<Page />);
    assert.equal(screen.queryByRole("heading", { name: "Notes and follow-up" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Edit" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Download this call (PDF)" }) !== null, true);
});

test("a read-only member sees the card without an Edit button", () => {
    grantedPermissions = ["recordings:read"];
    render(<Page />);
    assert.equal(screen.queryByRole("heading", { name: "Notes and follow-up" }) !== null, true);
    assert.equal(screen.queryByRole("button", { name: "Edit" }) === null, true);
    assert.equal(screen.queryByText("Your role cannot edit call details.") !== null, true);
});
