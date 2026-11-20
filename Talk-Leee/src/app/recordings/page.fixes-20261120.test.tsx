import Module from "node:module";
import assert from "node:assert/strict";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { extendedApi } from "@/lib/extended-api";

const user = { id: "user-1", tenant_id: "tenant-1", account_timezone: "Europe/London" };
const permissions = { isSuccess: true, isError: false, data: { permissions: ["recordings:read"] } };
const nothing = () => null;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ user }) };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/recordings/recording-media-controls")) return { RecordingMediaControls: nothing };
    if (id.includes("components/calls/recording-feedback-bar")) return { RecordingFeedbackBar: nothing };
    if (id.includes("lib/queries/inbound-queries")) return { useEffectivePermissions: () => permissions };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}
afterEach(() => { cleanup(); mock.restoreAll(); });

test("a recording's date is shown in the account time zone", async () => {
    mock.method(extendedApi, "listRecordings", async () => ({
        items: [{ id: "rec-1", call_id: "call-12345678", phone_number: "+447400123456", created_at: "2026-07-01T12:00:00Z", duration_seconds: 65, file_size_bytes: 1000, status: "ready", legal_hold: false }],
        total: 1, page: 1, page_size: 20,
    }) as never);
    render(<Page />);
    // 12:00 UTC on 1 July is 1:00 PM BST (GMT+1) in London.
    await waitFor(() => assert.equal(screen.queryByText(/1:00:00 PM GMT\+1/) !== null, true), { timeout: 5000 });
});
