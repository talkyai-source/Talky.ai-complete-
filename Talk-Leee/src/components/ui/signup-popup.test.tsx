import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";

// Real SignupPopup; only next/navigation is stubbed so the test can drive the
// route the component sees. `pathname` is mutable and read on every render.
let pathname = "/";
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => pathname };
    return originalLoad.call(this, id, ...args);
};
let SignupPopup: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above
    SignupPopup = require("./signup-popup").SignupPopup;
} finally {
    loader._load = originalLoad;
}

const DELAY = 10_000;

afterEach(() => {
    cleanup();
    pathname = "/";
});

function popup() {
    return document.querySelector<HTMLElement>("[data-signup-popup]");
}

function mount() {
    const originalSetTimeout = globalThis.setTimeout;
    const originalClearTimeout = globalThis.clearTimeout;
    // The first-load timer is counted from navigation start via performance.now();
    // pin it to 0 so the fake clock below is the only clock.
    const originalPerfNow = performance.now;
    performance.now = () => 0;
    let now = 0;
    let nextId = 1;
    const timers = new Map<number, { at: number; fn: () => void }>();
    (globalThis as unknown as { setTimeout: unknown }).setTimeout = ((fn: () => void, ms = 0) => {
        const id = nextId++;
        timers.set(id, { at: now + ms, fn });
        return id;
    }) as unknown;
    (globalThis as unknown as { clearTimeout: unknown }).clearTimeout = ((id: number) => {
        timers.delete(id);
    }) as unknown;
    const advance = (ms: number) => {
        const target = now + ms;
        for (;;) {
            const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
            if (!due) break;
            now = due[1].at;
            timers.delete(due[0]);
            act(() => due[1].fn());
        }
        now = target;
    };
    const utils = render(<SignupPopup />);
    const navigate = (to: string) => {
        pathname = to;
        utils.rerender(<SignupPopup />);
        // The arrival timer (0 ms for a page never cancelled) is async by design.
        advance(0);
    };
    const restore = () => {
        globalThis.setTimeout = originalSetTimeout;
        globalThis.clearTimeout = originalClearTimeout;
        performance.now = originalPerfNow;
    };
    return { advance, navigate, restore };
}

test("absent before 10 s, present after, on the first page", () => {
    const { advance, restore } = mount();
    try {
        advance(9_000);
        assert.ok(!popup(), "hidden at 9 s");
        advance(1_000);
        assert.ok(popup(), "shown at 10 s");
        assert.equal(
            popup()?.querySelector("p")?.textContent,
            "Ready to make your first call? Create an account to configure your AI voice agent.",
        );
        const buttons = [...popup()!.querySelectorAll<HTMLElement>("a, button")].map((el) => el.textContent);
        assert.deepEqual(buttons, ["Signup", "Cancel"]);
        assert.equal(popup()!.querySelector("a")?.getAttribute("href"), "/auth/register");
    } finally {
        restore();
    }
});

test("navigating before the timer fires does not restart it", () => {
    const { advance, navigate, restore } = mount();
    try {
        advance(5_000);
        navigate("/pricing");
        advance(4_999);
        assert.ok(!popup(), "still hidden at 9.999 s of the original timer");
        advance(1);
        assert.ok(popup(), "shown at 10 s counted from first load");
    } finally {
        restore();
    }
});

test("cancel hides it on that page only; a cancelled page waits 10 s on return", () => {
    const { advance, navigate, restore } = mount();
    try {
        advance(DELAY);
        navigate("/pricing");
        assert.ok(popup(), "shown on /pricing at once");
        act(() => {
            popup()!.querySelector("button")!.click();
        });
        assert.ok(!popup(), "hidden after Cancel");
        advance(60_000);
        assert.ok(!popup(), "stays hidden on the cancelled page");
        navigate("/contact");
        assert.ok(popup(), "another page shows it immediately");
        navigate("/pricing");
        assert.ok(!popup(), "cancelled page: hidden on arrival");
        advance(9_999);
        assert.ok(!popup(), "cancelled page: hidden at 9.999 s");
        advance(1);
        assert.ok(popup(), "cancelled page: shown at 10 s after arrival");
        // A second cancel re-arms the wait again.
        act(() => {
            popup()!.querySelector("button")!.click();
        });
        navigate("/contact");
        assert.ok(popup(), "never-cancelled page still immediate");
        navigate("/pricing");
        advance(9_999);
        assert.ok(!popup(), "second return: hidden at 9.999 s");
        advance(1);
        assert.ok(popup(), "second return: shown at 10 s");
    } finally {
        restore();
    }
});

test("leaving a cancelled page before its wait ends clears the wait", () => {
    const { advance, navigate, restore } = mount();
    try {
        advance(DELAY);
        navigate("/pricing");
        act(() => {
            popup()!.querySelector("button")!.click();
        });
        navigate("/contact");
        navigate("/pricing");
        advance(5_000);
        navigate("/contact");
        assert.ok(popup(), "shown on /contact");
        advance(5_000);
        assert.ok(popup(), "still on /contact, the stale /pricing timer did not fire into it");
        navigate("/pricing");
        advance(5_000);
        assert.ok(!popup(), "wait is counted from the new arrival");
    } finally {
        restore();
    }
});

test("never shown on the registration or login page, shown again on leaving", () => {
    const { advance, navigate, restore } = mount();
    try {
        advance(DELAY);
        assert.ok(popup());
        navigate("/auth/register");
        assert.ok(!popup(), "hidden on /auth/register");
        advance(60_000);
        assert.ok(!popup(), "never appears on /auth/register");
        navigate("/auth/login");
        assert.ok(!popup(), "hidden on /auth/login");
        navigate("/pricing");
        assert.ok(popup(), "back on /pricing immediately");
    } finally {
        restore();
    }
});

test("renders only on the allow-listed public routes, matched exactly", () => {
    const { advance, navigate, restore } = mount();
    try {
        advance(DELAY);
        assert.ok(popup(), "/ is permitted");
        navigate("/pricing");
        assert.ok(popup(), "/pricing is permitted");
        navigate("/contacts");
        assert.ok(!popup(), "/contacts is not permitted by /contact");
        navigate("/contact");
        assert.ok(popup(), "/contact is permitted");
        navigate("/dashboard");
        assert.ok(!popup(), "/dashboard is not permitted");
        navigate("/admin/billing");
        assert.ok(!popup(), "an /admin route is not permitted");
        navigate("/auth/forgot-password");
        assert.ok(!popup(), "/auth/forgot-password is not permitted");
        navigate("/no-such-page");
        assert.ok(!popup(), "an unknown route is not permitted");
        navigate("/docs/getting-started");
        assert.ok(popup(), "a listed docs page is permitted");
        navigate("/ai-options");
        assert.ok(!popup(), "/ai-options is not permitted by /ai-voices");
        navigate("/contact/");
        assert.ok(popup(), "the trailing-slash form the production router reports is permitted");
        navigate("/contacts/");
        assert.ok(!popup(), "/contacts/ is not permitted");
        advance(60_000);
        assert.ok(!popup(), "a non-permitted route never shows it, however long");
    } finally {
        restore();
    }
});

test("direct load on an auth page stays hidden past 10 s", () => {
    pathname = "/auth/login";
    const { advance, restore } = mount();
    try {
        advance(15_000);
        assert.ok(!popup());
    } finally {
        restore();
    }
});

test("writes nothing to storage", () => {
    const { advance, restore } = mount();
    try {
        advance(DELAY);
        act(() => {
            popup()!.querySelector("button")!.click();
        });
        assert.equal(window.localStorage.length, 0);
        assert.equal(window.sessionStorage.length, 0);
        assert.equal(document.cookie, "");
    } finally {
        restore();
    }
});
