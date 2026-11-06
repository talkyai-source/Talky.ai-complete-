import assert from "node:assert/strict";
import Module from "node:module";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { act, cleanup, render, screen } from "@testing-library/react";

// Both return pages (/connectors/callback and /connectors/[type]/callback) are
// loaded for real; only routing is stubbed so each test can set the query.
const loader = Module as unknown as { _load: (id: string, ...args: unknown[]) => unknown };
const originalLoad = loader._load;
let query = new URLSearchParams("");
const routeParams = { type: "calendar" };
loader._load = function (id, ...args) {
    if (id === "next/navigation") return { useSearchParams: () => query, useParams: () => routeParams };
    if (id === "next/link") {
        return {
            __esModule: true,
            default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
        };
    }
    return originalLoad.call(this, id, ...args);
};
let PlainPage: React.ComponentType;
let TypedPage: React.ComponentType;
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    PlainPage = require("./page").default;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- synchronous scoped boundary stubs above
    TypedPage = require("../[type]/callback/page").default;
} finally {
    loader._load = originalLoad;
}

// A failing assert.equal(<DOM node>, null) makes node:assert print the whole
// JSDOM node and never return, so a regression would hang instead of fail.
const absent = (node: unknown, message?: string) => assert.equal(node === null, true, message ?? "expected nothing to be rendered");

const GENERIC = "The connection couldn't be completed. Try again.";
const PAGES: Array<[string, () => React.ComponentType]> = [
    ["/connectors/callback", () => PlainPage],
    ["/connectors/[type]/callback", () => TypedPage],
];

let closes = 0;
const realClose = window.close;

beforeEach(() => {
    closes = 0;
    window.close = () => { closes += 1; };
    for (const store of [window.sessionStorage, window.localStorage]) {
        for (const key of ["connectors.callback.sent", "connectors.callback.eventId", "connectors.refresh", "connectors.refresh.event"]) store.removeItem(key);
    }
});

afterEach(() => {
    cleanup();
    window.close = realClose;
});

// Longer than the success page's 0.9 s auto-close.
const pastAutoClose = () => act(async () => { await new Promise((r) => setTimeout(r, 1300)); });

for (const [route, page] of PAGES) {
    test(`${route}: a failure shows its sentence and does not close itself`, async () => {
        query = new URLSearchParams("status=error&error=access_denied");
        render(React.createElement(page()));

        assert.ok(screen.getByText("Access wasn't granted. Try again and allow access."));
        assert.ok(screen.getByText("Connection failed"));
        assert.equal(screen.getByRole("link", { name: "Back to Connectors" }).getAttribute("href"), "/connectors");
        await pastAutoClose();
        assert.equal(closes, 0, "a failure page must stay open");
    });

    test(`${route}: a return with no status is not shown as connected`, async () => {
        query = new URLSearchParams("");
        render(React.createElement(page()));

        assert.ok(screen.getByText(GENERIC));
        assert.ok(screen.getByText("Connection failed"));
        absent(screen.queryByText("Connection complete"));
        absent(screen.queryByText(/connected successfully/i));
        await pastAutoClose();
        assert.equal(closes, 0);
    });

    test(`${route}: an unmapped code shows the plain sentence with the code in brackets`, async () => {
        query = new URLSearchParams("status=error&type=email&error=callback_failed");
        render(React.createElement(page()));

        assert.ok(screen.getByText(`${GENERIC} (callback_failed)`));
        await pastAutoClose();
        assert.equal(closes, 0);
    });

    test(`${route}: a reload of the return page does not send its message twice`, async () => {
        query = new URLSearchParams("status=error&type=email&error=access_denied");
        render(React.createElement(page()));
        await act(async () => {});
        // With no opener the message goes out through storage (and BroadcastChannel).
        const sent = JSON.parse(window.localStorage.getItem("connectors.refresh.event") ?? "null") as { type?: string; ok?: boolean } | null;
        assert.equal(sent?.type, "connectors:updated", "the first load sends");
        assert.equal(sent?.ok, false);
        assert.notEqual(window.localStorage.getItem("connectors.refresh"), null);

        // Reload: same tab, so sessionStorage survives; the page mounts again.
        cleanup();
        window.localStorage.removeItem("connectors.refresh.event");
        window.localStorage.removeItem("connectors.refresh");
        render(React.createElement(page()));
        await act(async () => {});
        assert.equal(window.localStorage.getItem("connectors.refresh.event"), null, "the reload must not send again");
        assert.equal(window.localStorage.getItem("connectors.refresh"), null);
        assert.ok(screen.getByText("Access wasn't granted. Try again and allow access."), "the reload still shows the sentence");
    });

    test(`${route}: a later popup that inherits this tab's sessionStorage sends a new id`, async () => {
        const sentId = () => (JSON.parse(window.localStorage.getItem("connectors.refresh.event") ?? "null") as { eventId?: unknown } | null)?.eventId;
        query = new URLSearchParams("status=error&type=email&error=access_denied");
        render(React.createElement(page()));
        await act(async () => {});
        const first = sentId();
        assert.equal(typeof first, "string");

        // The next popup opened from the same tab starts with a copy of its
        // sessionStorage. The Connectors page has cleared the "sent" mark by
        // then; everything else the earlier return page stored is still there.
        cleanup();
        window.localStorage.removeItem("connectors.refresh.event");
        window.sessionStorage.removeItem("connectors.callback.sent");
        render(React.createElement(page()));
        await act(async () => {});
        const second = sentId();
        assert.equal(typeof second, "string", "the later popup sends");
        assert.notEqual(second, first, "an id reused from an earlier return is dropped by the opener as a duplicate");
    });

    test(`${route}: a success still closes itself`, async () => {
        query = new URLSearchParams("status=success&type=calendar&provider=google_calendar");
        render(React.createElement(page()));

        assert.ok(screen.getByText("Connection complete"));
        assert.ok(screen.getByText("calendar connected successfully."));
        assert.equal(closes, 0);
        await pastAutoClose();
        assert.equal(closes, 1, "success keeps today's auto-close");
    });
}
