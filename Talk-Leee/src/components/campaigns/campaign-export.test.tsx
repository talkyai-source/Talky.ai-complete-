import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";
import type { Campaign } from "@/lib/dashboard-api";
import {
    CampaignPerformanceTable,
    campaignsToCsvWithColumns,
    csvFormulaGuard,
    EXPORT_FAILED_TEXT,
    printCampaignsWithColumns,
} from "./campaign-performance-table";

// Plan 3.4 E1 (PDF print view), E3 (formula guard) and E4 (visible export
// state) on the campaigns Export window.

const requestAnimationFrame = window.requestAnimationFrame;
const cancelAnimationFrame = window.cancelAnimationFrame;
const windowOpen = window.open;
const createObjectURL = URL.createObjectURL;
const revokeObjectURL = URL.revokeObjectURL;
const anchorClick = window.HTMLAnchorElement.prototype.click;
const clients: QueryClient[] = [];
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    window.requestAnimationFrame = requestAnimationFrame;
    window.cancelAnimationFrame = cancelAnimationFrame;
    window.open = windowOpen;
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    window.HTMLAnchorElement.prototype.click = anchorClick;
    localStorage.clear();
});

const running = {
    id: "c1", name: "Spring push", status: "running", direction: "outbound", system_prompt: "", voice_id: "v",
    max_concurrent_calls: 1, total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: "2026-01-01T00:00:00Z",
} as unknown as Campaign;
const paused = { ...running, id: "c2", name: "Autumn", status: "paused", total_leads: 5 } as Campaign;

/** A stand-in for the window `window.open` returns: a real document plus print/focus counters. */
function fakeWindow() {
    const doc = document.implementation.createHTMLDocument("");
    const calls = { print: 0, focus: 0 };
    const w = { document: doc, print() { calls.print++; }, focus() { calls.focus++; } } as unknown as Window;
    return { w, doc, calls };
}

/** Lets a download succeed in jsdom and records the file names handed to the browser. */
function allowDownloads() {
    const names: string[] = [];
    URL.createObjectURL = () => "blob:test";
    URL.revokeObjectURL = () => {};
    window.HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) { names.push(this.download); };
    return names;
}

async function mountTable(props: Partial<React.ComponentProps<typeof CampaignPerformanceTable>> = {}) {
    window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id) => window.clearTimeout(id);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(
        <AppRouterContext.Provider value={router}>
            <QueryClientProvider client={client}>
                <CampaignPerformanceTable
                    campaigns={[running, paused]}
                    loading={false}
                    error=""
                    onPause={async () => {}}
                    onStop={async () => {}}
                    onPauseSelected={async () => {}}
                    onResume={() => {}}
                    onDelete={async () => {}}
                    onDuplicate={async () => {}}
                    {...props}
                />
            </QueryClientProvider>
        </AppRouterContext.Provider>,
    );
    // Finish the table's initial preference hydration and selection-reset frames.
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
}

function openExportWindow() {
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    return within(screen.getByRole("dialog", { name: "Export & Reporting" }));
}

function statusText(dialog: ReturnType<typeof within>): string | null {
    return dialog.queryByTestId("campaign-export-status")?.textContent ?? null;
}

test("E3: a value beginning with =, +, - or @ is written with a leading apostrophe; every other value is unchanged", () => {
    for (const ch of ["=", "+", "-", "@"]) {
        const csv = campaignsToCsvWithColumns([{ ...running, name: `${ch}cmd()` }], ["name"]);
        assert.equal(csv.split("\n")[1], `'${ch}cmd()`, `name starting with ${ch}`);
        assert.equal(csvFormulaGuard(`${ch}1`), `'${ch}1`);
    }
    assert.equal(campaignsToCsvWithColumns([{ ...running, name: "Plain name" }], ["name"]).split("\n")[1], "Plain name");
    assert.equal(csvFormulaGuard("Plain name"), "Plain name");
    assert.equal(csvFormulaGuard("10"), "10");
    assert.equal(csvFormulaGuard(""), "");
    // The guard runs before the CSV quoting, so a quoted cell keeps its apostrophe.
    assert.equal(campaignsToCsvWithColumns([{ ...running, name: "=1,2" }], ["name"]).split("\n")[1], "\"'=1,2\"");
    // Numbers and the status label go out as before.
    assert.equal(campaignsToCsvWithColumns([running], ["status", "total_leads", "calls_completed"]).split("\n")[1], "Running,10,3");
});

test("E1: the print view receives the selected columns and rows, with status names, and asks the browser to print", () => {
    const { w, doc, calls } = fakeWindow();
    // As Chromium does: a window opened with "noopener" hands back no handle.
    window.open = (_url?: string | URL, _target?: string, features?: string) => (features?.includes("noopener") ? null : w);
    const ok = printCampaignsWithColumns([running, paused], ["name", "status", "total_leads"], "All time");
    assert.equal(ok, true);
    assert.deepEqual(Array.from(doc.querySelectorAll("thead th")).map((th) => th.textContent), ["name", "status", "total_leads"]);
    const rows = Array.from(doc.querySelectorAll("tbody tr")).map((tr) => Array.from(tr.querySelectorAll("td")).map((td) => td.textContent));
    assert.deepEqual(rows, [["Spring push", "Running", "10"], ["Autumn", "Paused", "5"]]);
    assert.equal(doc.querySelector("h1")?.textContent, "Campaign Export (All time)");
    assert.equal(calls.print, 1);
    assert.equal(calls.focus, 1);
});

test("E1: when the window cannot be opened (pop-ups blocked) the print export reports a failure instead of doing nothing", () => {
    window.open = () => null;
    assert.equal(printCampaignsWithColumns([running], ["name"], "All time"), false);
    window.open = () => { throw new Error("blocked"); };
    assert.equal(printCampaignsWithColumns([running], ["name"], "All time"), false);
});

test("E4: PDF success shows 'Print view opened.'", async () => {
    const { w } = fakeWindow();
    window.open = () => w;
    await mountTable();
    const dialog = openExportWindow();
    assert.equal(statusText(dialog), null);
    fireEvent.click(dialog.getByRole("button", { name: "PDF" }));
    assert.equal(statusText(dialog), "Print view opened.");
});

test("E4: PDF failure shows the failure text", async () => {
    window.open = () => null;
    await mountTable();
    const dialog = openExportWindow();
    fireEvent.click(dialog.getByRole("button", { name: "PDF" }));
    assert.equal(statusText(dialog), EXPORT_FAILED_TEXT);
    assert.equal(statusText(dialog), "The export failed. Try again.");
});

test("E4: CSV, Excel and JSON success show 'Downloaded {file name}.'", async () => {
    const names = allowDownloads();
    await mountTable();
    const dialog = openExportWindow();
    fireEvent.click(dialog.getByRole("button", { name: "CSV" }));
    assert.equal(statusText(dialog), "Downloaded campaigns-export.csv.");
    fireEvent.click(dialog.getByRole("button", { name: "Excel" }));
    assert.equal(statusText(dialog), "Downloaded campaigns-export.csv.");
    fireEvent.click(dialog.getByRole("button", { name: "JSON" }));
    assert.equal(statusText(dialog), "Downloaded campaigns-export.json.");
    assert.deepEqual(names, ["campaigns-export.csv", "campaigns-export.csv", "campaigns-export.json"]);
});

test("E4: a download that throws shows the failure text", async () => {
    URL.createObjectURL = () => { throw new Error("no blob urls"); };
    await mountTable();
    const dialog = openExportWindow();
    fireEvent.click(dialog.getByRole("button", { name: "CSV" }));
    assert.equal(statusText(dialog), EXPORT_FAILED_TEXT);
    fireEvent.click(dialog.getByRole("button", { name: "JSON" }));
    assert.equal(statusText(dialog), EXPORT_FAILED_TEXT);
});

test("E4: the page can own the Export window's open state (the command bar opens it this way)", async () => {
    const changes: boolean[] = [];
    await mountTable({ exportOpen: true, onExportOpenChange: (next) => changes.push(next) });
    assert.equal(screen.queryByRole("dialog", { name: "Export & Reporting" }) !== null, true);
    fireEvent.keyDown(window, { key: "Escape" });
    assert.deepEqual(changes, [false]);
});
