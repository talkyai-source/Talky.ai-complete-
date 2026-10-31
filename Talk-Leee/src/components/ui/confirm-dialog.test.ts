import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement, useEffect, useState } from "react";
import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
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

// A parent can close the dialog WITHOUT going through onOpenChange — the
// mutation succeeded elsewhere, the route changed, a bulk action finished. That
// path never reaches the internal reset, so `pending` / `inlineError` used to
// survive into the next open.
let setOpenFromParent: ((next: boolean) => void) | null = null;

function ParentControlledHarness({ onConfirm }: { onConfirm: () => void | Promise<void> }) {
    const [open, setOpen] = useState(true);
    useEffect(() => {
        setOpenFromParent = setOpen;
        return () => {
            setOpenFromParent = null;
        };
    }, []);
    return createElement(ConfirmDialog, {
        open,
        // The parent owns `open` and ignores the callback entirely.
        onOpenChange: () => {},
        intent: "disconnect",
        warningText: "Disconnect the connector?",
        onConfirm,
    });
}

async function reopenFromParent() {
    await act(async () => {
        setOpenFromParent?.(false);
    });
    await act(async () => {
        setOpenFromParent?.(true);
    });
}

test("ConfirmDialog clears the inline error when the parent closes it directly", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    renderWithQueryClient(
        createElement(ParentControlledHarness, {
            onConfirm: () => {
                throw new Error("Nope");
            },
        })
    );

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => assert.ok(screen.getByText("Nope")));

    await reopenFromParent();

    assert.ok(screen.getByRole("dialog"));
    assert.equal(screen.queryByText("Nope"), null);
});

test("ConfirmDialog clears the pending spinner when the parent closes it directly", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    renderWithQueryClient(
        createElement(ParentControlledHarness, {
            // Never settles: the dialog stays in its pending state until
            // something else resets it.
            onConfirm: () => new Promise<void>(() => {}),
        })
    );

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => assert.ok(screen.getByRole("button", { name: /Disconnect\.\.\./ })));

    await reopenFromParent();

    const confirm = within(screen.getByRole("dialog")).getByRole("button", { name: "Disconnect" });
    assert.equal((confirm as HTMLButtonElement).disabled, false);
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
