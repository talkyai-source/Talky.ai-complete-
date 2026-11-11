import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

// Owner decision 12 Nov 2026: the hero demo talks to every visitor; it no
// longer sends signed-out visitors to the sign-in page. The router records
// where the popup tries to send the visitor, and the visitor is signed out
// for any build of the popup that still asks.
const pushes: string[] = [];
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") {
        return { useRouter: () => ({ push: (href: string) => { pushes.push(href); }, replace() {} }), usePathname: () => "/" };
    }
    if (id === "@/lib/auth-context") return { useAuth: () => ({ status: "unauthenticated" }) };
    return originalLoad.call(this, id, ...args);
};
let VoiceAgentPopup: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    VoiceAgentPopup = require("./voice-agent-popup").VoiceAgentPopup;
} finally {
    loader._load = originalLoad;
}

afterEach(() => {
    cleanup();
    pushes.length = 0;
    sessionStorage.clear();
});

test("a signed-out visitor's click starts the demo instead of sending them to sign in", async () => {
    render(<VoiceAgentPopup />);
    fireEvent.click(screen.getByRole("button", { name: "Ask AI" }));
    // The test browser has no microphone, so a session that really started
    // stops at the microphone check: that message proves the click started it.
    await waitFor(() => assert.ok(screen.getByText(/support microphone access/i)));
    assert.deepEqual(pushes, [], "the visitor stays on the page");
    assert.equal(sessionStorage.getItem("talky.askai.signin"), null, "no sign-in notice is queued");
});
