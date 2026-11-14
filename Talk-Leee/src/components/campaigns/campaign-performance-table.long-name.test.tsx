/**
 * FX4b Task 2 and FX4b2 Task 1 — on the campaigns list a long name gets a
 * wider column below 1280px, where the table scrolls inside its card anyway
 * (the name track is floored at 15rem, not 10rem); from 1280px up the table
 * fits its card and the name takes the room left. Where it is cut the full
 * name is on the name button: in its title (hover) and in its accessible name
 * (keyboard focus).
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { CampaignPerformanceTable } from "@/components/campaigns/campaign-performance-table";
import type { Campaign } from "@/lib/dashboard-api";

const LONG = "Spring renewal reminders for lapsed customers in the northern region, second attempt.";
const router = { push() {}, replace() {}, refresh() {}, back() {}, forward() {}, async prefetch() {} } as unknown as React.ContextType<typeof AppRouterContext>;
const campaigns = [{
    id: "c0", name: LONG, status: "running", direction: "outbound", system_prompt: "", voice_id: "v", max_concurrent_calls: 1,
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

test("the full name is in the name button's title and in its accessible name", () => {
    assert.equal(LONG.length, 85, "the measured 85-character name");
    mount();
    const nameButton = screen.getByText(LONG).closest("button");
    assert.equal(nameButton !== null, true);
    assert.equal(nameButton!.getAttribute("title"), LONG);
    assert.equal((nameButton!.textContent ?? "").includes(LONG), true, "the button's own text, which names it, holds the full name");
    assert.equal(nameButton!.hasAttribute("aria-label"), false, "nothing replaces that name");
});

const classStartingWith = (element: Element, prefix: string) => Array.from(element.classList).find((name) => name.startsWith(prefix)) ?? "";
/** The fixed tracks and gutters of one row, in rem: everything but the name track. */
const FIXED_REM = (36 + 110 + 170 + 120 + 90 + 90 + 90) / 16 + 7 * 0.5 + 2 * 0.75;

test("below 1280px the name column is floored at 15rem and the header row uses the same columns", () => {
    mount();
    const rows = screen.getAllByRole("row");
    assert.equal(rows.length >= 2, true, "header row and one campaign row");
    const template = (row: Element) => classStartingWith(row, "grid-cols-[");
    assert.equal(template(rows[0]).includes("minmax(15rem,1.6fr)"), true);
    assert.equal(template(rows[0]).includes("minmax(10rem"), false);
    assert.equal(rows.every((row) => template(row) === template(rows[0])), true);
    // The table's floor is exactly its columns: the 15rem name track plus the fixed ones.
    assert.equal(classStartingWith(rows[0], "min-w-["), "min-w-[" + (15 + FIXED_REM) + "rem]");
});

test("from 1280px up the table's floor is under the 945px card, so it does not scroll inside it", () => {
    mount();
    const rows = screen.getAllByRole("row");
    const wideTemplate = (row: Element) => classStartingWith(row, "xl:grid-cols-[");
    const nameFloor = /minmax\(([\d.]+)rem,1\.6fr\)/.exec(wideTemplate(rows[0]));
    assert.equal(nameFloor !== null, true, "the name track still takes the room left (1.6fr) over a floor");
    assert.equal(rows.every((row) => wideTemplate(row) === wideTemplate(rows[0])), true, "header and rows share the columns");
    // The fixed tracks are the same as below 1280px.
    assert.equal(wideTemplate(rows[0]).replace(/^xl:/, "").replace(/minmax\([^)]*\)/, ""), classStartingWith(rows[0], "grid-cols-[").replace(/minmax\([^)]*\)/, ""));
    const floorRem = Number(nameFloor![1]) + FIXED_REM;
    // Header row, and each campaign row's bordered wrapper, carry the floor.
    const carriers = [rows[0], rows[1].parentElement!];
    assert.equal(carriers.every((element) => classStartingWith(element, "xl:min-w-[") === "xl:min-w-[" + floorRem + "rem]"), true, "the floor is exactly the columns");
    // 945px: the card's inner width at 1280px with the sidebar expanded (measured, FX4b).
    assert.equal(floorRem * 16 <= 945, true, floorRem * 16 + "px must fit 945px");
});
