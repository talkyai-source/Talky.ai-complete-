/**
 * FX7b Task 1.3 — /sentry-example-page/ raises one uncaught browser error for
 * a signed-in visitor, shows nothing of its own, and is reachable only by
 * typing its address.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import { afterEach, mock, test } from "node:test";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { PUBLIC_ROUTES } from "@/lib/public-routes";

let currentUser: { id: string } | null = null;
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id.includes("components/layout/dashboard-layout")) {
        return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <div data-testid="layout">{children}</div> };
    }
    if (id.includes("hooks/useAuth")) return { useAuth: () => ({ user: currentUser }) };
    return originalLoad.call(this, id, ...args);
};
let Page: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Page = require("./page").default;
} finally {
    loader._load = originalLoad;
}

const MESSAGE = "Sentry Example Frontend Error";

// The page's timer is the window's own. It is captured here and run by hand,
// so the error it throws is seen by the test, not by the test runner.
type Pending = { id: number; run: () => void; delay: number };
let pending: Pending[] = [];
function captureWindowTimers() {
    let nextId = 1000;
    mock.method(window, "setTimeout", (run: () => void, delay?: number) => {
        const id = nextId++;
        pending.push({ id, run, delay: delay ?? 0 });
        return id;
    });
    mock.method(window, "clearTimeout", (id?: number) => {
        pending = pending.filter((timer) => timer.id !== id);
    });
}

afterEach(() => {
    cleanup();
    mock.restoreAll();
    pending = [];
    currentUser = null;
});

test("signed in: one uncaught error with the exact message is due shortly after the page loads, outside React", () => {
    captureWindowTimers();
    currentUser = { id: "user-1" };
    render(<Page />);
    assert.equal(pending.length, 1);
    assert.equal(pending[0]!.delay > 0 && pending[0]!.delay <= 2000, true, "shortly after load");
    assert.throws(() => pending[0]!.run(), (error: unknown) => error instanceof Error && error.message === MESSAGE);
});

test("signed out: nothing is scheduled, so nothing is thrown", () => {
    captureWindowTimers();
    currentUser = null;
    render(<Page />);
    assert.equal(pending.length, 0);
});

test("leaving before the error is due cancels it", () => {
    captureWindowTimers();
    currentUser = { id: "user-1" };
    const view = render(<Page />);
    assert.equal(pending.length, 1);
    view.unmount();
    assert.equal(pending.length, 0);
});

test("the page shows no text of its own, inside the signed-in layout", () => {
    captureWindowTimers();
    currentUser = { id: "user-1" };
    const { container, getByTestId } = render(<Page />);
    assert.equal(getByTestId("layout").childNodes.length, 0);
    assert.equal(container.textContent, "");
});

test("the page is not in the sitemap's route list and nothing in the app links to it", () => {
    assert.equal((PUBLIC_ROUTES as readonly string[]).some((route) => route.includes("sentry-example-page")), false);
    const root = path.join(process.cwd(), "src");
    const found: string[] = [];
    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) { walk(full); continue; }
            if (!/\.tsx?$/.test(full) || /\.(test|stories)\./.test(full)) continue;
            const file = path.relative(root, full).split(path.sep).join("/");
            if (file.startsWith("app/sentry-example-page/")) continue;
            if (fs.readFileSync(full, "utf8").includes("sentry-example-page")) found.push(file);
        }
    };
    walk(root);
    // robots.ts names the address only to disallow it (W8); no link or import may.
    assert.deepEqual(found, ["app/robots.ts"]);
    const robots = fs.readFileSync(path.join(root, "app/robots.ts"), "utf8");
    assert.match(robots, /^\s*"\/sentry-example-page",$/m);
});
