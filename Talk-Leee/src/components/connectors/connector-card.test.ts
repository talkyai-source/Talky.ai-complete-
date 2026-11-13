import { test, afterEach, mock } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { cleanup, fireEvent, screen, within, act } from "@testing-library/react";
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { ConnectorCard } from "@/components/connectors/connector-card";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

afterEach(() => { cleanup(); mock.restoreAll(); });

test("ConnectorCard enables only Connect when disconnected", () => {
    renderWithQueryClient(
        createElement(ConnectorCard, {
            type: "email",
            name: "Email",
            description: "Connect inboxes",
            icon: Mail,
            status: "disconnected",
        })
    );

    assert.equal(screen.getByRole("button", { name: "Connect" }).hasAttribute("disabled"), false);
    assert.equal(screen.queryByRole("button", { name: "Reconnect" }), null);
    assert.equal(screen.queryByRole("button", { name: "Disconnect" }), null);
});

test("ConnectorCard enables Disconnect when connected", () => {
    renderWithQueryClient(
        createElement(ConnectorCard, {
            type: "email",
            name: "Email",
            description: "Connect inboxes",
            icon: Mail,
            status: "connected",
        })
    );

    assert.equal(screen.getByRole("button", { name: "Disconnect" }).hasAttribute("disabled"), false);
    assert.equal(screen.queryByRole("button", { name: "Connect" }), null);
    assert.equal(screen.queryByRole("button", { name: "Reconnect" }), null);
});

test("ConnectorCard shows Expired status and allows reconnect", () => {
    renderWithQueryClient(
        createElement(ConnectorCard, {
            type: "email",
            name: "Email",
            description: "Connect inboxes",
            icon: Mail,
            status: "expired",
        })
    );

    assert.ok(screen.getByLabelText("Status: Expired"));
    assert.equal(screen.getByRole("button", { name: "Reconnect" }).hasAttribute("disabled"), false);
    assert.equal(screen.getByRole("button", { name: "Disconnect" }).hasAttribute("disabled"), false);
    assert.equal(screen.queryByRole("button", { name: "Connect" }), null);
});

test("ConnectorCard exposes an unavailable server capability without a dead OAuth button", () => {
    renderWithQueryClient(
        createElement(ConnectorCard, {
            type: "salesforce",
            name: "Salesforce",
            description: "Sync qualified leads",
            icon: Mail,
            status: "disconnected",
            available: false,
            unavailableReason: "Salesforce is not enabled by this server yet.",
        })
    );

    assert.ok(screen.getByLabelText("Status: Unavailable"));
    assert.ok(screen.getByText("Salesforce is not enabled by this server yet."));
    assert.ok(screen.getByText("Server capability required"));
    assert.equal(screen.queryByRole("button", { name: "Connect" }), null);
});

test("ConnectorCard calls authorize and shows loading state", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    const calls: string[] = [];
    const prevOpen = window.open;
    window.open = ((url: string) => {
        calls.push(url);
        return { focus: () => {} } as unknown as Window;
    }) as typeof window.open;

    const authorizeConnector = async () => {
        await new Promise((r) => setTimeout(r, 50));
        return { authorization_url: "https://provider.example/auth" };
    };

    try {
        renderWithQueryClient(
            createElement(ConnectorCard, {
                type: "email",
                name: "Email",
                description: "Connect inboxes",
                icon: Mail,
                status: "disconnected",
                authorizeConnector,
            })
        );

        await act(async () => {
            await user.click(screen.getByTestId("connector-email-connect"));
        });

        await act(async () => {
            await new Promise((r) => setTimeout(r, 70));
        });

        assert.deepEqual(calls, ["https://provider.example/auth"]);
        assert.ok(screen.getByRole("button", { name: "Connect" }));
    } finally {
        window.open = prevOpen;
    }
});

test("ConnectorCard confirms and calls disconnect", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    let disconnected = 0;

    renderWithQueryClient(
        createElement(ConnectorCard, {
            type: "email",
            name: "Email",
            description: "Connect inboxes",
            icon: Mail,
            status: "connected",
            disconnectConnector: async () => {
                disconnected += 1;
            },
        })
    );

    await act(async () => {
        await user.click(screen.getByRole("button", { name: "Disconnect" }));
    });

    const dialog = screen.getByRole("dialog");
    await act(async () => {
        await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    });

    assert.equal(disconnected, 1);
});

// A failing assert.equal(<DOM node>, null) makes node:assert print the whole
// JSDOM node and never return, so a regression would hang instead of fail.
const absent = (node: unknown, message?: string) => assert.equal(node === null, true, message ?? "expected nothing to be rendered");

// ---- Plan 2.5: no false status, labels, OAuth failure sentences, popup closed ----

const CHECKING = "Checking status…";
const CLOSED_EARLY = "The connection window was closed before finishing. Nothing was changed.";
const ACCESS_DENIED = "Access wasn't granted. Try again and allow access.";
const EXPIRED_TEXT = "Credentials have expired. Reconnect to refresh access.";

type CardProps = Parameters<typeof ConnectorCard>[0];
const baseProps = { type: "email" as const, name: "Email", description: "Connect inboxes", icon: Mail };

function cardElement(qc: QueryClient, props: Partial<CardProps>) {
    return createElement(QueryClientProvider, { client: qc }, createElement(ConnectorCard, { ...baseProps, status: "disconnected", ...props } as CardProps));
}

const wait = (ms: number) => act(async () => { await new Promise((r) => setTimeout(r, ms)); });

/** A stand-in for the OAuth window, as the opener's handle reports it. */
function fakePopup() {
    const popup = { closed: false, where: "blank" as "blank" | "provider" };
    const win = {
        get closed() {
            return popup.closed;
        },
        focus() {},
        location: {
            get href() {
                if (popup.where === "provider") throw new Error("SecurityError: cross-origin");
                return "about:blank";
            },
        },
    } as unknown as Window;
    return { popup, win };
}

async function withOpen<T>(result: Window | null, run: () => Promise<T>) {
    const prevOpen = window.open;
    window.open = (() => result) as typeof window.open;
    try {
        return await run();
    } finally {
        window.open = prevOpen;
    }
}

const authorizeOk = async () => ({ authorization_url: "https://provider.example/auth" });

test("a card still waiting for the status answer shows only 'Checking status…'", () => {
    renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: null, checking: true }));

    assert.ok(screen.getByText(CHECKING));
    absent(screen.queryByLabelText(/^Status:/), "no status pill before the backend answers");
    assert.equal(screen.queryAllByRole("button").length, 0, "no Connect, Reconnect or Disconnect before the backend answers");
    absent(screen.queryByText("Disconnected."));
});

test("a card with no status answer shows no status, no buttons and no message of its own", () => {
    renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: null }));

    absent(screen.queryByLabelText(/^Status:/));
    assert.equal(screen.queryAllByRole("button").length, 0);
    absent(screen.queryByText(CHECKING), "a failed status call is not still checking");
    absent(screen.queryByText("Disconnected."));
});

test("backend status error is labelled 'Needs attention', never 'Error' or 'Failed'", () => {
    renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "error" }));

    assert.ok(screen.getByLabelText("Status: Needs attention"));
    absent(screen.queryByLabelText("Status: Error"));
    absent(screen.queryByText("Failed"));
    assert.equal(screen.getByRole("button", { name: "Reconnect" }).hasAttribute("disabled"), false);
});

test("a connected card says 'Connected.', not 'Syncing enabled.'", () => {
    renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "connected" }));

    assert.ok(screen.getByLabelText("Status: Connected"));
    assert.ok(screen.getByText("Connected."));
    absent(screen.queryByText("Syncing enabled."));
});

test("the token refresh time is labelled 'Last refreshed' and its value is unchanged", () => {
    const refreshedAt = "2026-01-05T09:30:00Z";
    renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "connected", lastSync: refreshedAt }));

    assert.ok(screen.getByText("Last refreshed"));
    absent(screen.queryByText("Last sync"));
    assert.ok(screen.getByText(new Date(Date.parse(refreshedAt)).toLocaleString()));
});

test("an expired card with no backend message says what to do next", () => {
    const view = renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "expired" }));

    assert.ok(screen.getByText(EXPIRED_TEXT));
    absent(screen.queryByText("Connection needs attention."));
    assert.equal(screen.getByRole("button", { name: "Reconnect" }).hasAttribute("disabled"), false);

    view.rerender(cardElement(view.qc, { status: "expired", errorMessage: "Token revoked by the account owner." }));
    assert.ok(screen.getByText("Token revoked by the account owner."));
    absent(screen.queryByText(EXPIRED_TEXT), "a backend message wins over the default");
});

test("closing the OAuth window before any return shows the closed sentence and refetches status", async () => {
    const { popup, win } = fakePopup();
    await withOpen(win, async () => {
        const view = renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "disconnected", authorizeConnector: authorizeOk }));
        const refetch = mock.method(view.qc, "invalidateQueries");

        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        popup.where = "provider";
        await wait(700);
        absent(screen.queryByText(CLOSED_EARLY), "an open window is not a closed one");
        assert.equal(screen.getByRole("button", { name: "Connect" }).hasAttribute("disabled"), false, "the Connect wait ended when the window opened");

        popup.closed = true;
        await wait(700);
        assert.ok(screen.getByText(CLOSED_EARLY));
        assert.equal(refetch.mock.callCount(), 1);
        assert.deepEqual(refetch.mock.calls[0]!.arguments[0], { queryKey: ["connectorStatuses"] });
        assert.ok(screen.getByLabelText("Status: Disconnected"), "the close itself does not change the status");
    });
});

test("a handle that reads closed without being seen on the provider shows nothing", async () => {
    // Under Cross-Origin-Opener-Policy: same-origin the handle reads closed
    // while the window is still open; that must not be reported as a close.
    const { popup, win } = fakePopup();
    await withOpen(win, async () => {
        const view = renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "disconnected", authorizeConnector: authorizeOk }));
        const refetch = mock.method(view.qc, "invalidateQueries");

        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        popup.closed = true;
        await wait(1200);
        absent(screen.queryByText(CLOSED_EARLY));
        assert.equal(refetch.mock.callCount(), 0);
        assert.ok(screen.getByText("Disconnected."));
    });
});

test("a return message ends the wait: its failure sentence shows and a later close adds nothing", async () => {
    const { popup, win } = fakePopup();
    await withOpen(win, async () => {
        const opened: string[] = [];
        const onOAuthWindowOpened = (type: string) => { opened.push(type); };
        const view = renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "disconnected", authorizeConnector: authorizeOk, onOAuthWindowOpened }));
        const refetch = mock.method(view.qc, "invalidateQueries");

        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        assert.deepEqual(opened, ["email"], "the card reports that it opened the window");
        popup.where = "provider";
        await wait(700);

        view.rerender(cardElement(view.qc, { authorizeConnector: authorizeOk, onOAuthWindowOpened, oauthReturn: { id: 1, ok: false, providerType: "email", message: ACCESS_DENIED } }));
        assert.ok(screen.getByText(ACCESS_DENIED));
        assert.ok(screen.getByLabelText("Status: Disconnected"), "the message does not change the status");

        popup.closed = true;
        await wait(1200);
        assert.ok(screen.getByText(ACCESS_DENIED));
        absent(screen.queryByText(CLOSED_EARLY));
        assert.equal(refetch.mock.callCount(), 0, "the card's own watch ended with the return message");
    });
});

test("a return message shows only on the matching card, and a success clears the sentence", () => {
    const view = renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "disconnected" }));

    view.rerender(cardElement(view.qc, { oauthReturn: { id: 1, ok: false, providerType: "calendar", message: ACCESS_DENIED } }));
    absent(screen.queryByText(ACCESS_DENIED), "another card's return");

    view.rerender(cardElement(view.qc, { oauthReturn: { id: 2, ok: false, message: ACCESS_DENIED } }));
    absent(screen.queryByText(ACCESS_DENIED), "a return that names no card");

    view.rerender(cardElement(view.qc, { oauthReturn: { id: 3, ok: false, providerType: "email", message: ACCESS_DENIED } }));
    assert.ok(screen.getByText(ACCESS_DENIED));
    assert.ok(screen.getByLabelText("Status: Disconnected"));

    view.rerender(cardElement(view.qc, { oauthReturn: { id: 4, ok: true, providerType: "email", message: "email connected successfully." } }));
    absent(screen.queryByText(ACCESS_DENIED));
    assert.ok(screen.getByLabelText("Status: Disconnected"), "a success message does not mark the card connected");
    absent(screen.queryByText("email connected successfully."));
});

test("the closed sentence is dropped once the backend reports a different status", async () => {
    const { popup, win } = fakePopup();
    await withOpen(win, async () => {
        const view = renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "disconnected", authorizeConnector: authorizeOk }));
        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        popup.where = "provider";
        await wait(700);
        popup.closed = true;
        await wait(700);
        assert.ok(screen.getByText(CLOSED_EARLY));

        view.rerender(cardElement(view.qc, { status: "connected", authorizeConnector: authorizeOk }));
        absent(screen.queryByText(CLOSED_EARLY), "'Nothing was changed' must not sit on a card the backend now reports differently");
        assert.ok(screen.getByText("Connected."));
    });
});

test("a blocked popup falls back to this window and the Connect wait ends", async () => {
    const prevHash = window.location.hash;
    await withOpen(null, async () => {
        renderWithQueryClient(
            createElement(ConnectorCard, {
                ...baseProps,
                status: "disconnected",
                // A hash-only URL: jsdom can follow it, so the fallback is observable.
                authorizeConnector: async () => ({ authorization_url: "http://localhost/#oauth-fallback" }),
            })
        );
        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        await wait(1200);
        assert.equal(window.location.hash, "#oauth-fallback");
        assert.equal(screen.getByRole("button", { name: "Connect" }).hasAttribute("disabled"), false);
        absent(screen.queryByText(CLOSED_EARLY));
    });
    window.location.hash = prevHash;
});

test("a Connect that cannot start ends with the error and a usable button", async () => {
    await withOpen(null, async () => {
        renderWithQueryClient(
            createElement(ConnectorCard, {
                ...baseProps,
                status: "disconnected",
                authorizeConnector: async () => {
                    throw new Error("Request timed out");
                },
            })
        );
        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        await wait(50);
        assert.ok(screen.getByText("Request timed out"));
        assert.equal(screen.getByRole("button", { name: "Connect" }).hasAttribute("disabled"), false);
    });
});

// ── FX3a Task 4.3: one ellipsis style ────────────────────────────────────────
test("FX3a: Connect reads Starting… with the ellipsis character while the request runs", async () => {
    const never = () => new Promise<never>(() => {});
    renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "disconnected", authorizeConnector: never } as CardProps));
    await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
    const label = screen.getByTestId("connector-email-connect").textContent ?? "";
    assert.equal(label.includes("Starting…"), true, label);
    assert.equal(label.includes("..."), false, label);
});

// ── FX3a2 Tasks 3.1 and 5 ────────────────────────────────────────────────────
test("FX3a2: the provider line shows the connector's name, not the provider id", () => {
    renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, name: "Gmail", status: "connected", provider: "gmail" } as CardProps));
    assert.equal(screen.queryByText("Provider: Gmail") !== null, true);
    assert.equal(screen.queryByText("Provider: gmail") !== null, false);
});

test("FX3a2: the app's own time limit reads Please retry., a server message is shown as is", async () => {
    const { ApiClientError } = await import("@/lib/http-client");
    const failures: unknown[] = [
        new ApiClientError({ code: "timeout", message: "Request timed out", url: "/connectors/email/authorize", method: "GET" }),
        new ApiClientError({ status: 503, code: "server_error", message: "Connector service is unavailable", url: "/connectors/email/authorize", method: "GET" }),
    ];
    await withOpen(null, async () => {
        renderWithQueryClient(createElement(ConnectorCard, { ...baseProps, status: "disconnected", authorizeConnector: async () => { throw failures.shift(); } } as CardProps));
        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        await wait(50);
        assert.equal(screen.queryByText("Please retry.") !== null, true);
        assert.equal(screen.queryByText("Request timed out") !== null, false);
        await act(async () => { fireEvent.click(screen.getByTestId("connector-email-connect")); });
        await wait(50);
        assert.equal(screen.queryByText("Connector service is unavailable") !== null, true);
    });
});
