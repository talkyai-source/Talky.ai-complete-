/**
 * FX5 Task B1 — the pages whose features the server does not serve answer
 * with the app's not-found page, and nothing in the app links to them. Their
 * code stays in the repository.
 *
 * FX5b Task 1 — the server serves the action history (GET /assistant/actions),
 * so /assistant/actions is shown again and the proposal card links to it.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import Module from "node:module";
import path from "node:path";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import NotFound from "@/app/not-found";
import { EditProposalCard, type ProposalData } from "@/components/assistant/edit-proposal-card";

const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
// The page bodies are never reached; their layout and data hooks are not needed.
loader._load = function (id, ...args) {
    if (id.includes("components/assistant/action-history")) return { ActionHistory: () => <div data-testid="action-history" /> };
    if (id.includes("components/layout/dashboard-layout")) return { DashboardLayout: ({ children }: { children: React.ReactNode }) => <div data-testid="page-body">{children}</div> };
    return originalLoad.call(this, id, ...args);
};
const pages: Record<string, React.ComponentType> = {};
let ActionsPage: React.ComponentType;
try {
    /* eslint-disable @typescript-eslint/no-require-imports -- synchronous scoped boundary stub above */
    pages["/email"] = require("./email/page").default;
    pages["/reminders"] = require("./reminders/page").default;
    pages["/meetings"] = require("./meetings/page").default;
    pages["/assistant"] = require("./assistant/page").default;
    ActionsPage = require("./assistant/actions/page").default;
    pages["/assistant/meetings"] = require("./assistant/meetings/page").default;
    pages["/assistant/reminders"] = require("./assistant/reminders/page").default;
    /* eslint-enable @typescript-eslint/no-require-imports */
} finally {
    loader._load = originalLoad;
}

afterEach(cleanup);

/** Stands in for Next's not-found boundary: shows the app's not-found page for a not-found signal. */
class NotFoundBoundary extends React.Component<{ children: React.ReactNode }, { digest: string | null }> {
    state = { digest: null as string | null };
    static getDerivedStateFromError(error: unknown) {
        return { digest: String((error as { digest?: unknown }).digest ?? (error as Error).message ?? "") };
    }
    render() {
        if (this.state.digest === null) return this.props.children;
        return /NEXT_HTTP_ERROR_FALLBACK;404|NEXT_NOT_FOUND/.test(this.state.digest)
            ? <NotFound />
            : <div data-testid="other-error">{this.state.digest}</div>;
    }
}

for (const [route, Page] of Object.entries(pages)) {
    test(`${route} answers with the not-found page`, (context) => {
        context.mock.method(console, "error", () => {});
        render(<NotFoundBoundary><Page /></NotFoundBoundary>);
        assert.equal(screen.queryByText("Page not found") !== null, true, "the app's not-found page");
        assert.equal(screen.queryByTestId("page-body") === null, true, "the page body is not rendered");
        assert.equal(screen.queryByTestId("other-error") === null, true);
    });
}

test("/assistant/actions is served: it shows the action history, not the not-found page", () => {
    render(<NotFoundBoundary><ActionsPage /></NotFoundBoundary>);
    assert.equal(screen.queryByTestId("action-history") !== null, true, "the action history is rendered");
    assert.equal(screen.queryByText("Page not found") === null, true);
    assert.equal(screen.queryByTestId("other-error") === null, true);
});

test("the pages' code is still in the repository", () => {
    const app = path.join(process.cwd(), "src", "app");
    for (const route of Object.keys(pages)) {
        const source = fs.readFileSync(path.join(app, route.slice(1), "page.tsx"), "utf8");
        assert.equal(source.includes("notFound();"), true, route);
        assert.equal(source.split("\n").length > 8, true, `${route} keeps its code`);
    }
    assert.match(fs.readFileSync(path.join(app, "email", "page.tsx"), "utf8"), /<EmailContent \/>/);
    assert.match(fs.readFileSync(path.join(app, "meetings", "page.tsx"), "utf8"), /<MeetingsContent \/>/);
});

test("no link, menu entry or navigation in the app points to a hidden page", () => {
    const root = path.join(process.cwd(), "src");
    const hiddenDirs = ["app/email/", "app/reminders/", "app/meetings/", "app/assistant/"];
    const target = String.raw`\/(?:email|reminders|meetings|assistant)(?:\/(?:meetings|reminders))?\/?(?=["'\`?#])`;
    const patterns = [
        new RegExp(String.raw`href\s*[=:]\s*\{?\s*["'\`]` + target),
        new RegExp(String.raw`(?:push|replace|redirect|permanentRedirect)\(\s*["'\`]` + target),
    ];
    const found: string[] = [];
    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) { walk(full); continue; }
            if (!/\.tsx?$/.test(full) || /\.(test|stories)\./.test(full)) continue;
            const file = path.relative(root, full).split(path.sep).join("/");
            if (hiddenDirs.some((hidden) => file.startsWith(hidden))) continue;
            const lines = fs.readFileSync(full, "utf8").split("\n");
            lines.forEach((line, index) => {
                if (patterns.some((pattern) => pattern.test(line))) found.push(`${file}:${index + 1}`);
            });
        }
    };
    walk(root);
    assert.deepEqual(found, []);
});

test("the assistant's proposal card links to the actions page, and to no hidden page", () => {
    const proposal = { proposalId: "proposal-unknown", tool: "send_email", changes: [], status: "unknown", actionId: "action-owned-1" } as unknown as ProposalData;
    render(<EditProposalCard proposal={proposal} onApply={() => {}} onReject={() => {}} />);
    assert.equal(screen.queryByText(/Check the saved action record before sending again/) !== null, true, "the existing sentence stays");
    const link = screen.queryByRole("link", { name: "View actions" });
    assert.equal(link !== null, true);
    assert.equal(link?.getAttribute("href"), "/assistant/actions");
    assert.equal(document.querySelectorAll('a[href^="/assistant"]').length, 1, "no other assistant link");
});
