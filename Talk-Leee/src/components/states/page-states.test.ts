import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { cleanup, screen } from "@testing-library/react";
import { EmptyState, ErrorState, LoadingSkeleton } from "@/components/states/page-states";
import { ensureDom } from "@/test-utils/dom";
import { renderWithQueryClient } from "@/test-utils/render";

ensureDom();

afterEach(() => cleanup());

test("ErrorState renders message and supports retry", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    let retries = 0;
    renderWithQueryClient(
        createElement(ErrorState, {
            title: "Oops",
            message: "Failed",
            onRetry: () => {
                retries += 1;
            },
        })
    );

    assert.ok(screen.getByText("Oops"));
    assert.ok(screen.getByRole("alert"));
    assert.ok(screen.getByText("Try these quick checks"));

    await user.click(screen.getByRole("button", { name: "Retry" }));
    assert.equal(retries, 1);
});

test("ErrorState renders support action when provided", () => {
    renderWithQueryClient(
        createElement(ErrorState, {
            title: "Oops",
            message: "Failed",
            supportHref: "/support",
            supportLabel: "Contact support",
        })
    );

    const link = screen.getByRole("link", { name: "Contact support" });
    assert.equal(link.getAttribute("href"), "/support");
});

test("EmptyState renders CTA when provided", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    let clicks = 0;
    renderWithQueryClient(
        createElement(EmptyState, {
            title: "Nothing here",
            message: "No data",
            actionLabel: "Create",
            onAction: () => {
                clicks += 1;
            },
        })
    );

    await user.click(screen.getByRole("button", { name: "Create" }));
    assert.equal(clicks, 1);
});

test("EmptyState supports primary and secondary actions", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    const user = userEvent.setup({ document: globalThis.document });
    let secondary = 0;

    renderWithQueryClient(
        createElement(EmptyState, {
            title: "No items",
            message: "Create or import.",
            hint: "Start by adding your first item.",
            primaryActionLabel: "Create new",
            onPrimaryAction: () => {},
            secondaryActionLabel: "Import",
            onSecondaryAction: () => {
                secondary += 1;
            },
        })
    );

    assert.ok(screen.getByText("Start by adding your first item."));
    await user.click(screen.getByRole("button", { name: "Import" }));
    assert.equal(secondary, 1);
});

test("LoadingSkeleton renders requested number of lines", () => {
    const { container } = renderWithQueryClient(createElement(LoadingSkeleton, { lines: 2, showAction: false }));
    const lines = Array.from(container.querySelectorAll("div")).filter((el) => el.className.includes("animate-pulse") && el.className.includes("h-4"));
    assert.equal(lines.length, 2);
});

test("LoadingSkeleton list variant renders rows", () => {
    const { container } = renderWithQueryClient(createElement(LoadingSkeleton, { variant: "list", items: 3, showAction: false }));
    const circles = Array.from(container.querySelectorAll("div")).filter((el) => el.className.includes("animate-pulse") && el.className.includes("rounded-full"));
    assert.equal(circles.length, 3);
});
