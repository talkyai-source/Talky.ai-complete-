import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement, useState } from "react";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

afterEach(() => cleanup());

function Harness({ onConfirm }: { onConfirm: () => void | Promise<void> }) {
    const [open, setOpen] = useState(true);
    return createElement(ConfirmDialog, {
        open,
        onOpenChange: setOpen,
        intent: "disconnect",
        warningText: "Disconnect the connector?",
        onConfirm,
    });
}

function IntentHarness({ intent }: { intent: "disconnect" | "cancel" | "delete" }) {
    const [open, setOpen] = useState(true);
    return createElement(ConfirmDialog, {
        open,
        onOpenChange: setOpen,
        intent,
        warningText: "Are you sure?",
        onConfirm: () => {},
    });
}

test("ConfirmDialog focuses Cancel and traps tab navigation", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    renderWithQueryClient(createElement(Harness, { onConfirm: () => {} }));

    await new Promise((r) => setTimeout(r, 0));
    const cancel = screen.getByRole("button", { name: "Cancel" });
    assert.equal(document.activeElement, cancel);

    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Disconnect" });
    confirm.focus();
    assert.equal(document.activeElement, confirm);

    await user.tab();
    assert.equal(document.activeElement, cancel);
});

test("ConfirmDialog calls onConfirm and closes on success", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    let called = 0;
    renderWithQueryClient(
        createElement(Harness, {
            onConfirm: () => {
                called += 1;
            },
        })
    );

    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Disconnect" });
    await user.click(confirm);

    assert.equal(called, 1);
    await waitFor(() => {
        assert.equal(screen.queryByRole("dialog"), null);
    });
});

test("ConfirmDialog closes on Escape", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    renderWithQueryClient(createElement(Harness, { onConfirm: () => {} }));

    await user.keyboard("{Escape}");
    await new Promise((r) => setTimeout(r, 0));
    assert.equal(screen.queryByRole("dialog"), null);
});

test("ConfirmDialog shows error when confirm fails and stays open", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    renderWithQueryClient(
        createElement(Harness, {
            onConfirm: () => {
                throw new Error("Nope");
            },
        })
    );

    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Disconnect" });
    await user.click(confirm);

    await waitFor(() => {
        assert.ok(screen.getByText("Nope"));
        assert.ok(screen.getByRole("dialog"));
    });
});

test("ConfirmDialog intent=cancel uses default copy", () => {
    renderWithQueryClient(createElement(IntentHarness, { intent: "cancel" }));
    assert.ok(screen.getByText("Cancel action"));
    assert.ok(screen.getByText("This will stop the current operation."));
    assert.equal(screen.getAllByRole("button", { name: "Cancel" }).length, 2);
});

test("ConfirmDialog intent=delete uses default copy", () => {
    renderWithQueryClient(createElement(IntentHarness, { intent: "delete" }));
    assert.ok(screen.getByText("Delete item"));
    assert.ok(screen.getByText("This action cannot be undone."));
    assert.ok(screen.getByRole("button", { name: "Delete" }));
});
