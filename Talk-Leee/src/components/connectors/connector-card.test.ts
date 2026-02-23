import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { cleanup, screen, within } from "@testing-library/react";
import { Mail } from "lucide-react";
import { ConnectorCard } from "@/components/connectors/connector-card";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

afterEach(() => cleanup());

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

        await user.click(screen.getByTestId("connector-email-connect"));

        await new Promise((r) => setTimeout(r, 70));

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

    await user.click(screen.getByRole("button", { name: "Disconnect" }));

    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    assert.equal(disconnected, 1);
});
