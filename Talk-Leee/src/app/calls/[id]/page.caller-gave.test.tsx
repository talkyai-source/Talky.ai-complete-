/**
 * Call page: the "Caller gave" tile shows the email and number the caller
 * gave; "Not provided" when the server sent null; nothing at all when an older
 * server did not send the fields. Same boundary stubs as page.fx3b-text.test.tsx.
 */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";

type Q = Record<string, unknown>;
let callState: Q = {};
let transcriptState: Q = {};
const call = {
    id: "call-1", status: "completed", direction: "inbound", phone_number: "+447400123456", to_number: "+442079460999",
    created_at: "2026-01-01T00:00:00Z", duration_seconds: 65, outcome: "no_answer",
};

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    // The call page now saves notes through this hook and reads lead details with useQuery; neither is under test here.
    if (id.endsWith("lib/use-call-history-workflow")) return { useCallHistoryWorkflow: () => ({ workflow: {}, update: () => {}, retry: () => {}, busy: false, failed: false, backupUnavailable: false }) };
    if (id === "@tanstack/react-query") return { ...(originalLoad.call(this, id, ...args) as object), useQuery: () => ({ data: undefined }) };
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user: { id: "user-1", tenant_id: "tenant-1" } }) };
    if (id === "next/navigation") return { useParams: () => ({ id: "call-1" }), useRouter: () => ({ push: () => {}, back: () => {} }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/voice-feedback-recorder")) return { VoiceFeedbackRecorder: nothing };
    if (id.includes("components/calls/conversation-review-panel")) return { ConversationReviewPanel: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { LeadDetailsPanel: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: true, isError: false, data: { permissions: [] } }) };
    if (id.endsWith("lib/api-hooks")) return { useCall: () => callState, useCallTranscript: () => transcriptState };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const originalRequestAnimationFrame = window.requestAnimationFrame;
const originalCancelAnimationFrame = window.cancelAnimationFrame;
const originalScrollTo = window.Element.prototype.scrollTo;
beforeEach(() => {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
    window.Element.prototype.scrollTo = () => {};
});
afterEach(() => {
    cleanup(); mock.restoreAll(); callState = {}; transcriptState = {};
    window.requestAnimationFrame = originalRequestAnimationFrame;
    window.cancelAnimationFrame = originalCancelAnimationFrame;
    window.Element.prototype.scrollTo = originalScrollTo;
});

function mount(extra: Q) {
    callState = { data: { ...call, ...extra }, isLoading: false, isError: false };
    transcriptState = { data: { turns: [] }, isLoading: false, isError: false, error: null, refetch: () => {} };
    return render(<Page />);
}

test("fields sent with values are shown in the Caller gave tile", () => {
    const { container } = mount({ captured_email: "gave@example.com", captured_phone: "+923085397539" });
    const tile = container.querySelector('[data-caller-gave="tile"]');
    assert.equal(tile !== null, true);
    assert.equal((tile!.textContent ?? "").includes("gave@example.com"), true, tile!.textContent ?? "");
    assert.equal((tile!.textContent ?? "").includes("+92 308 5397539"), true, tile!.textContent ?? "");
});

test("fields sent as null say Not provided twice", () => {
    const { container } = mount({ captured_email: null, captured_phone: null });
    const tile = container.querySelector('[data-caller-gave="tile"]');
    assert.equal(tile !== null, true);
    assert.equal(((tile!.textContent ?? "").match(/Not provided/g) ?? []).length, 2, tile!.textContent ?? "");
});

test("fields not sent by an older server show no Caller gave tile at all", () => {
    const { container } = mount({});
    assert.equal(container.querySelector("[data-caller-gave]") !== null, false);
    assert.equal((container.textContent ?? "").includes("Not provided"), false);
});
