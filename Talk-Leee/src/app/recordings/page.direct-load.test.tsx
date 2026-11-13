/** FX1a Task 1 — a direct load or refresh of /recordings shows its data without any click. */
import Module from "node:module";
import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { screen } from "@testing-library/react";
import { expectDirectLoadShowsData, restoreDirectLoad } from "@/test-utils/direct-load";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
const nothing = () => null;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <>{children}</> };
    if (id.includes("components/recordings/recording-media-controls")) return { RecordingMediaControls: nothing };
    if (id.includes("components/calls/recording-feedback-bar")) return { RecordingFeedbackBar: nothing };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

afterEach(restoreDirectLoad);

const recording = {
    id: "rec-1", call_id: "call-12345678", phone_number: "+447400123456", created_at: "2026-01-01T00:00:00Z",
    duration_seconds: 65, file_size_bytes: 1000, status: "ready", legal_hold: false,
};

test("direct load of /recordings waits for the sign-in check and then shows the recordings", async () => {
    await expectDirectLoadShowsData(<Page />, [
        [/\/rbac\/users\/me\/permissions$/, { permissions: ["recordings:read"] }],
        [/\/recordings$/, { items: [recording], total: 1, page: 1, page_size: 20 }],
    ], () => screen.queryByText("+447400123456") !== null);
    assert.equal(screen.queryByText(/Recording permissions could not be verified/) === null, true);
});
