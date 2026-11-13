import { afterEach, mock, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PrintButton, PrintInLightTheme } from "./print-support";

// QA F1-24 (QA-UI-034): printing a call page included the sidebar, header
// and floating buttons, there were no print styles, and a dark-theme user
// would print light text. Pages now print as their content, light, with a
// Print button on call detail (invoices already had one).
afterEach(() => { cleanup(); mock.restoreAll(); document.documentElement.className = ""; });

const source = (file: string) => readFileSync(path.join(process.cwd(), file), "utf8");

test("the Print button opens the browser's print dialog and is not itself printed", () => {
    const print = mock.method(window, "print", () => {});
    render(<PrintButton />);
    const button = screen.getByRole("button", { name: "Print" });
    assert.match(button.className, /print:hidden/);
    fireEvent.click(button);
    assert.equal(print.mock.callCount(), 1);
});

test("a dark-theme page prints in the light theme and returns to dark afterwards", () => {
    document.documentElement.classList.add("dark");
    render(<PrintInLightTheme />);
    act(() => { window.dispatchEvent(new window.Event("beforeprint")); });
    assert.equal(document.documentElement.classList.contains("dark"), false);
    assert.equal(document.documentElement.classList.contains("light"), true);
    act(() => { window.dispatchEvent(new window.Event("afterprint")); });
    assert.equal(document.documentElement.classList.contains("dark"), true);
    assert.equal(document.documentElement.classList.contains("light"), false);
});

test("a light-theme page is left as it is", () => {
    document.documentElement.classList.add("light");
    render(<PrintInLightTheme />);
    act(() => { window.dispatchEvent(new window.Event("beforeprint")); });
    act(() => { window.dispatchEvent(new window.Event("afterprint")); });
    assert.equal(document.documentElement.className, "light");
});

test("the app shell, call detail controls and invoice actions are hidden in print", () => {
    const layout = source("src/components/layout/dashboard-layout.tsx");
    for (const shell of [/<div className="print:hidden">\s*<Sidebar \/>/, /<div className="print:hidden">\s*\{\/\* Floating text assistant/,
        /className="[^"]*print:hidden[^"]*">\s*<HealthIndicator \/>/, /<main id="main-content" className="[^"]*print:overflow-visible/]) {
        assert.match(layout, shell);
    }
    assert.match(source("src/app/layout.tsx"), /<PrintInLightTheme \/>/);
    const call = source("src/app/calls/[id]/page.tsx");
    assert.match(call, /<PrintButton \/>/);
    assert.match(source("src/components/billing/invoice-view.tsx"), /<div className="flex flex-wrap gap-3 print:hidden">/);
    assert.match(source("src/app/globals.css"), /@media print \{[^}]*\.content-card \{[^}]*break-inside: avoid/);
});
