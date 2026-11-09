import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SkipToContent } from "./skip-to-content";

// QA F12-09 / F1-10 (QA-CMP-017, QA-UI-166): there was no skip link, so a
// keyboard user tabbed through the whole navigation on every page.
afterEach(() => cleanup());

function skip() {
    fireEvent.click(screen.getByRole("link", { name: "Skip to main content" }));
}

test("the skip link moves focus to #main-content where a page has it", () => {
    render(<><SkipToContent /><nav><a href="/x">Menu</a></nav><main id="main-content"><p>Body</p></main></>);
    skip();
    assert.equal(document.activeElement?.id, "main-content");
});

test("without #main-content it moves focus to the page's <main>", () => {
    render(<><SkipToContent /><main data-testid="page-main"><p>Body</p></main></>);
    skip();
    assert.equal(document.activeElement, screen.getByTestId("page-main"));
});

test("a page with neither falls back to its first heading", () => {
    render(<><SkipToContent /><div><h1>Sign in</h1></div></>);
    skip();
    assert.equal(document.activeElement, screen.getByRole("heading", { name: "Sign in" }));
});

test("it is the first thing in every page, and the signed-in layout names its main content", () => {
    const root = readFileSync(path.join(process.cwd(), "src/app/layout.tsx"), "utf8");
    assert.match(root, /<body[^>]*>\s*<SkipToContent \/>/);
    const dashboard = readFileSync(path.join(process.cwd(), "src/components/layout/dashboard-layout.tsx"), "utf8");
    assert.match(dashboard, /<main id="main-content"/);
});
