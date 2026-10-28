import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import { afterEach, test } from "node:test";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";

// Real Navbar; only routing, theme and auth are stubbed so the test exercises
// the drawer's own close/focus behaviour.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { usePathname: () => "/pricing", useRouter: () => ({ push() {}, prefetch() {} }) };
    if (id.includes("providers/theme-provider")) return { useTheme: () => ({ theme: "light", setTheme() {}, toggleTheme() {} }) };
    if (id.includes("lib/auth-context")) return { useAuth: () => ({ accessToken: null, setToken() {}, status: "anonymous", user: null }) };
    return originalLoad.call(this, id, ...args);
};
let Navbar: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    Navbar = require("./navbar").Navbar;
} finally {
    loader._load = originalLoad;
}

afterEach(() => cleanup());

function openDrawer(container: HTMLElement) {
    const details = container.querySelector<HTMLDetailsElement>("details.navbarMobileMenu");
    assert.ok(details, "mobile drawer <details> rendered");
    const summary = details.querySelector<HTMLElement>("summary.home-menu-toggle");
    assert.ok(summary, "drawer toggle rendered");
    act(() => {
        summary.focus();
        details.open = true;
        details.dispatchEvent(new window.Event("toggle"));
    });
    assert.equal(details.open, true);
    return { details, summary };
}

test("the X button closes the drawer and hands focus back to the toggle", () => {
    const { container } = render(<Navbar />);
    const { details, summary } = openDrawer(container);
    const close = container.querySelector<HTMLButtonElement>('button[aria-label="Close navigation menu"]');
    assert.ok(close, "close button rendered");
    act(() => {
        close.focus();
    });
    assert.ok(document.activeElement === close, "close button focused");
    act(() => {
        close.click();
    });
    assert.equal(details.open, false, "drawer closed");
    assert.ok(
        document.activeElement === summary,
        `focus returned to the Open navigation menu toggle (got ${document.activeElement?.tagName} ${document.activeElement?.getAttribute("aria-label") ?? ""})`,
    );
});

test("Escape pressed while focus is inside the drawer returns focus to the toggle", () => {
    const { container } = render(<Navbar />);
    const { details, summary } = openDrawer(container);
    const link = details.querySelector<HTMLAnchorElement>('.home-mobile-panel a[href="/"]');
    assert.ok(link, "drawer Talk-Lee link rendered");
    act(() => {
        link.focus();
    });
    assert.ok(document.activeElement === link, "drawer link focused");
    act(() => {
        window.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    assert.equal(details.open, false, "drawer closed");
    assert.ok(
        document.activeElement === summary,
        `focus returned to the Open navigation menu toggle (got ${document.activeElement?.tagName} ${document.activeElement?.getAttribute("aria-label") ?? ""})`,
    );
});

test("the open drawer's toggle does not intercept pointer hits (guard on globals.css)", () => {
    const css = fs.readFileSync(path.join(process.cwd(), "src", "app", "globals.css"), "utf8");
    assert.match(
        css,
        /details\[open\]\s*>\s*summary\.home-menu-toggle\s*\{[^}]*pointer-events:\s*none/,
        "details[open] > summary.home-menu-toggle must set pointer-events: none so the drawer's own Talk-Lee link is hit-testable",
    );
});
