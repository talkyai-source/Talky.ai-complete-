import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { backendApi } from "@/lib/backend-api";
import { AssistantRunSchema } from "@/lib/models";
import { ActionHistory } from "./action-history";

// Plan 3.4 E4 on the action-history export: the same three texts, inline
// next to its buttons.

const originalList = backendApi.assistantRuns.list;
const createObjectURL = URL.createObjectURL;
const revokeObjectURL = URL.revokeObjectURL;
const anchorClick = window.HTMLAnchorElement.prototype.click;
afterEach(() => {
    cleanup();
    backendApi.assistantRuns.list = originalList;
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    window.HTMLAnchorElement.prototype.click = anchorClick;
});

function allowDownloads() {
    const names: string[] = [];
    URL.createObjectURL = () => "blob:test";
    URL.revokeObjectURL = () => {};
    window.HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) { names.push(this.download); };
    return names;
}

async function show() {
    backendApi.assistantRuns.list = async (query) => ({
        items: [AssistantRunSchema.parse({ id: "record-1", type: "update_campaign_config", triggered_by: "assistant_tool",
            status: "completed", created_at: "2026-10-05T00:00:00Z" })], total: 1, page: query.page, page_size: 50,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { gcTime: 0 } } });
    render(<QueryClientProvider client={client}><ActionHistory /></QueryClientProvider>);
    await screen.findByRole("button", { name: "View record" });
    await waitFor(() => assert.equal(screen.getByRole("button", { name: "Export page CSV" }).hasAttribute("disabled"), false));
}

function statusText(): string | null {
    return screen.queryByTestId("action-history-export-status")?.textContent ?? null;
}

test("CSV and JSON success show 'Downloaded {file name}.' next to the buttons", async () => {
    const names = allowDownloads();
    await show();
    assert.equal(statusText(), null);
    fireEvent.click(screen.getByRole("button", { name: "Export page CSV" }));
    assert.equal(statusText(), "Downloaded action-history-page.csv.");
    fireEvent.click(screen.getByRole("button", { name: "Export page JSON" }));
    assert.equal(statusText(), "Downloaded action-history-page.json.");
    assert.deepEqual(names, ["action-history-page.csv", "action-history-page.json"]);
    // The status sits in the same row as the export buttons.
    const row = screen.getByRole("button", { name: "Export page CSV" }).parentElement;
    assert.equal(row?.contains(screen.getByTestId("action-history-export-status")), true);
});

test("a download that throws shows the failure text", async () => {
    URL.createObjectURL = () => { throw new Error("no blob urls"); };
    await show();
    fireEvent.click(screen.getByRole("button", { name: "Export page CSV" }));
    assert.equal(statusText(), "The export failed. Try again.");
    fireEvent.click(screen.getByRole("button", { name: "Export page JSON" }));
    assert.equal(statusText(), "The export failed. Try again.");
});
