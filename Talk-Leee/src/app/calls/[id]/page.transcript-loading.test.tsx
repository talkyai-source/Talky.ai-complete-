/** FX1b Task 2 — while the transcript loads the page says so, never "No transcript available". */
import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

let transcriptState: Record<string, unknown> = {};

const call = {
    id: "call-1", status: "completed", direction: "outbound", phone_number: "+447400123456",
    created_at: "2026-01-01T00:00:00Z", duration_seconds: 65, recording_id: null,
};

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    // Their side reads the signed-in user for the account timezone.
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user: { id: "user-1", tenant_id: "tenant-1" } }) };
    if (id === "next/navigation") return { useParams: () => ({ id: "call-1" }), useRouter: () => ({ push: () => {}, back: () => {} }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/calls/voice-feedback-recorder")) return { VoiceFeedbackRecorder: nothing };
    if (id.includes("components/calls/conversation-review-panel")) return { ConversationReviewPanel: nothing };
    if (id.includes("components/calls/lead-details-panel")) return { LeadDetailsPanel: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => ({ isSuccess: true, isError: false, data: { permissions: [] } }) };
    if (id.endsWith("lib/api-hooks")) {
        return {
            useCall: () => ({ data: call, isLoading: false, isError: false, error: null }),
            useCallTranscript: () => transcriptState,
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

afterEach(cleanup);

test("while the transcript loads the page shows Loading transcript…, not No transcript available", () => {
    transcriptState = { data: undefined, isLoading: true, isError: false, error: null, refetch: () => {} };
    render(<Page />);
    assert.equal(screen.queryByText("Loading transcript…") !== null, true);
    assert.equal(screen.queryByText("No transcript available") !== null, false);
});

test("a loaded call with no turns still says No transcript available", () => {
    transcriptState = { data: { turns: [] }, isLoading: false, isError: false, error: null, refetch: () => {} };
    render(<Page />);
    assert.equal(screen.queryByText("No transcript available") !== null, true);
    assert.equal(screen.queryByText("Loading transcript…") !== null, false);
});
