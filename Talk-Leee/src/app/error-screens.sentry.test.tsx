/**
 * FX7b Task 1.2 — the nine page-level error screens report the error they
 * were given to Sentry, the way global-error.tsx does, and still show what
 * they showed before.
 */
import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

const SCREENS = [
    "assistant",
    "assistant/actions",
    "connectors",
    "contacts",
    "meetings",
    "reminders",
    "support",
    "support/tickets",
    "support/tickets/[id]",
] as const;

const captured: unknown[] = [];
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "@sentry/nextjs") return { captureException: (error: unknown) => { captured.push(error); } };
    return originalLoad.call(this, id, ...args);
};
type ErrorScreen = React.ComponentType<{ error: Error & { digest?: string }; reset: () => void }>;
const screens = new Map<string, ErrorScreen>();
try {
    for (const route of SCREENS) {
        // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
        screens.set(route, require(`./${route}/error`).default);
    }
} finally {
    loader._load = originalLoad;
}

afterEach(() => {
    cleanup();
    captured.length = 0;
});

for (const route of SCREENS) {
    test(`/${route} error screen reports its error once and shows the same screen`, () => {
        const Screen = screens.get(route)!;
        const error = new Error(`failure on ${route}`);
        const view = render(<Screen error={error} reset={() => {}} />);
        assert.equal(captured.length, 1, "reported once");
        assert.equal(captured[0] === error, true, "the error it was given");
        assert.equal(screen.queryByText("Something went wrong") !== null, true, "the existing title");

        view.rerender(<Screen error={error} reset={() => {}} />);
        assert.equal(captured.length, 1, "the same error is not reported again");

        const next = new Error("a later failure");
        view.rerender(<Screen error={next} reset={() => {}} />);
        assert.equal(captured.length, 2, "a new error is reported");
        assert.equal(captured[1] === next, true);
    });
}
