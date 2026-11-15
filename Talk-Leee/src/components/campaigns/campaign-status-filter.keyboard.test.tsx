/**
 * FX4a Task 2 and Task 4 — on the campaigns list the search box is named by
 * its visible "Campaign name" label, and the Status filter is a keyboard
 * listbox: opening it moves focus to the first option, the arrow keys move
 * between options, Space toggles the focused one, Escape closes it and returns
 * focus to its button, and Tab past either end leaves it.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignPerformanceTable } from "@/components/campaigns/campaign-performance-table";
import type { Campaign } from "@/lib/dashboard-api";

const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const campaigns = [{
    id: "c0", name: "Running one", status: "running", direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
    total_leads: 10, calls_completed: 3, calls_failed: 1, created_at: "2026-01-01T00:00:00Z",
}] as unknown as Campaign[];
const clients: QueryClient[] = [];
const w = window as unknown as { requestAnimationFrame?: (cb: FrameRequestCallback) => number; cancelAnimationFrame?: (id: number) => void };
if (typeof w.requestAnimationFrame !== "function") {
    w.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0) as unknown as number;
    w.cancelAnimationFrame = (id) => clearTimeout(id);
}

afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); localStorage.clear(); });

function mount() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: Infinity } } });
    clients.push(client);
    const idle = async () => {};
    render(
        <QueryClientProvider client={client}>
            <AppRouterContext.Provider value={router}>
                <CampaignPerformanceTable
                    campaigns={campaigns} loading={false} error=""
                    onPause={idle} onStop={idle} onPauseSelected={idle} onResume={idle} onDelete={idle} onDuplicate={idle}
                />
            </AppRouterContext.Provider>
        </QueryClientProvider>,
    );
}

const filterButton = () => screen.getByRole("button", { name: /All statuses|selected/ });
const options = () => within(screen.getByRole("listbox")).getAllByRole("option");
const focusIsOn = (element: Element | null | undefined) => element !== null && element !== undefined && document.activeElement === element;
const isOpen = () => screen.queryByRole("listbox") !== null;
function open() {
    fireEvent.click(filterButton());
    return options();
}

test("the campaign search box is named by its visible label", () => {
    mount();
    const box = screen.getByRole("textbox", { name: "Campaign name" });
    assert.equal(box.getAttribute("placeholder"), "Search campaigns…");
});

test("the status filter is a multi-select listbox named Status; every option reports its state", () => {
    mount();
    assert.equal(filterButton().getAttribute("aria-expanded"), "false");
    const all = open();
    assert.equal(filterButton().getAttribute("aria-expanded"), "true");
    const listbox = screen.getByRole("listbox", { name: "Status" });
    assert.equal(listbox.getAttribute("aria-multiselectable"), "true");
    assert.deepEqual(all.map((option) => option.textContent), ["Draft", "Running", "Paused", "Completed", "Stopped"]);
    assert.deepEqual(all.map((option) => option.getAttribute("aria-selected")), all.map(() => "false"));
    // Clear and Done are buttons beside the listbox, not children of it.
    assert.equal(within(listbox).queryAllByRole("button").length, 0);
});

test("opening moves focus to the first option, which is the only option in the Tab order", () => {
    mount();
    const all = open();
    assert.equal(focusIsOn(all[0]), true);
    assert.deepEqual(all.map((option) => option.getAttribute("tabindex")), ["0", "-1", "-1", "-1", "-1"]);
});

test("the arrow keys, Home and End move between the options", () => {
    mount();
    const all = open();
    fireEvent.keyDown(all[0]!, { key: "ArrowDown" });
    assert.equal(focusIsOn(all[1]), true, "ArrowDown");
    assert.equal(all[1]!.getAttribute("tabindex"), "0", "the Tab stop follows the focus");
    fireEvent.keyDown(all[1]!, { key: "ArrowDown" });
    assert.equal(focusIsOn(all[2]), true);
    fireEvent.keyDown(all[2]!, { key: "ArrowUp" });
    assert.equal(focusIsOn(all[1]), true, "ArrowUp");
    fireEvent.keyDown(all[1]!, { key: "End" });
    assert.equal(focusIsOn(all[4]), true, "End");
    fireEvent.keyDown(all[4]!, { key: "ArrowDown" });
    assert.equal(focusIsOn(all[4]), true, "stays on the last option");
    fireEvent.keyDown(all[4]!, { key: "Home" });
    assert.equal(focusIsOn(all[0]), true, "Home");
});

test("Space toggles the focused option and keeps the filter open", () => {
    mount();
    const all = open();
    fireEvent.keyDown(all[0]!, { key: "ArrowDown" });
    fireEvent.keyDown(all[1]!, { key: " " });
    assert.equal(options()[1]!.getAttribute("aria-selected"), "true");
    assert.equal(isOpen(), true);
    assert.equal(filterButton().textContent, "1 selected");
    fireEvent.keyDown(options()[1]!, { key: " " });
    assert.equal(options()[1]!.getAttribute("aria-selected"), "false");
    assert.equal(filterButton().textContent, "All statuses");
});

test("Escape closes the filter and returns focus to its button", () => {
    mount();
    const all = open();
    fireEvent.keyDown(all[0]!, { key: "ArrowDown" });
    fireEvent.keyDown(all[1]!, { key: "Escape" });
    assert.equal(isOpen(), false);
    assert.equal(filterButton().getAttribute("aria-expanded"), "false");
    assert.equal(focusIsOn(filterButton()), true);
});

test("Tab past either end leaves the filter; Tab inside it does not close it", () => {
    mount();
    let all = open();
    // From an option, Tab goes on to Clear and Done: the filter stays open.
    fireEvent.keyDown(all[0]!, { key: "Tab" });
    assert.equal(isOpen(), true);
    // Tab on the last control closes it; the browser then moves on from the button.
    const done = screen.getByRole("button", { name: "Done" });
    done.focus();
    fireEvent.keyDown(done, { key: "Tab" });
    assert.equal(isOpen(), false);
    assert.equal(focusIsOn(filterButton()), true);

    all = open();
    fireEvent.keyDown(all[0]!, { key: "Tab", shiftKey: true });
    assert.equal(isOpen(), false, "Shift+Tab on the first stop");
    assert.equal(focusIsOn(filterButton()), true);
});

test("Done closes the filter and returns focus to its button", () => {
    mount();
    open();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    assert.equal(isOpen(), false);
    assert.equal(focusIsOn(filterButton()), true);
});
