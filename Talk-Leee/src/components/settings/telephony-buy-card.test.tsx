/**
 * Settings → Telephony: the "Buy Now" card, its documents window, the four
 * upload boxes and the two Submit buttons. Nothing here may send a request.
 */
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import React from "react";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "@/lib/api";
import { notificationsStore } from "@/lib/notifications";
import { NotificationToaster } from "@/components/notifications/notification-toaster";
import { TelephonyBuyCard } from "./telephony-buy-card";

const CARD_LINES = [
    "Minute charges vary by destination. Local conditions apply.",
    "Info: Each destination has its own regulations, which may vary from region to region.",
];
const TITLE = "Documentation is required:";
const DESCRIPTION = "To buy a phone number, we need the documents that destination regulations require. Choose business use or personal use and upload both documents.";
const BUSINESS = "1. Business Use";
const PERSONAL = "2. Personal Use";
const ITEMS: Record<string, [string, string]> = {
    [BUSINESS]: ["A) Business registration", "B) Photo ID matching the business registration"],
    [PERSONAL]: ["A) Utility bill (proof of address)", "B) Photo ID matching the utility bill"],
};
const EMPTY = "Click to upload or drag and drop";
const MESSAGE = "Thank you. Your documents have been submitted.";

const request = api.request;
const realFetch = globalThis.fetch;
let apiCalls = 0;
let fetchCalls = 0;

beforeEach(() => {
    apiCalls = 0;
    fetchCalls = 0;
    api.request = (async () => { apiCalls += 1; return {}; }) as typeof api.request;
    globalThis.fetch = (async () => { fetchCalls += 1; return new Response("{}"); }) as typeof fetch;
    notificationsStore.setIdentity({ tenantId: "buy-card", userId: "someone" });
});
afterEach(() => {
    cleanup();
    api.request = request;
    globalThis.fetch = realFetch;
    notificationsStore.setIdentity(null);
});

const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
const file = (name: string) => new File(["x"], name, { type: "application/pdf" });
const buyNow = () => screen.getByRole("button", { name: "Buy Now" }) as HTMLButtonElement;
const dialog = () => screen.getByRole("dialog", { name: TITLE });
const isOpen = () => screen.queryByRole("dialog", { name: TITLE }) !== null;
const card = (name: string) => within(dialog()).getByRole("region", { name });
const boxes = (name: string) => Array.from(card(name).querySelectorAll<HTMLElement>("[data-upload-box]"));
const allBoxes = () => [...boxes(BUSINESS), ...boxes(PERSONAL)];
const input = (box: HTMLElement) => box.querySelector('input[type="file"]') as HTMLInputElement;
const choose = (box: HTMLElement, chosen: File) => fireEvent.change(input(box), { target: { files: [chosen] } });
const shownName = (box: HTMLElement) => box.querySelector("[data-file-name]")?.textContent ?? null;
const submit = (name: string) => within(card(name)).getByRole("button", { name: "Submit" }) as HTMLButtonElement;
const focusIsOnBuyNow = () => document.activeElement === buyNow();

function mount() {
    render(<><TelephonyBuyCard /><NotificationToaster /></>);
}
async function openWindow() {
    buyNow().focus();
    fireEvent.click(buyNow());
    await settle();
}
/** How many times the hidden file input was asked to open the file picker. */
function pickerOpens(box: HTMLElement) {
    const counter = { count: 0 };
    input(box).addEventListener("click", () => { counter.count += 1; });
    return counter;
}

test("the card shows both lines word for word, above the Buy Now button, with no heading", () => {
    mount();
    const root = document.querySelector("[data-telephony-buy-card]") as HTMLElement;
    const lines = Array.from(root.querySelectorAll("p"));
    assert.deepEqual(lines.map((line) => line.textContent), CARD_LINES);
    for (const line of lines) {
        assert.equal(Boolean(line.compareDocumentPosition(buyNow()) & Node.DOCUMENT_POSITION_FOLLOWING), true, "Buy Now comes after the line");
    }
    assert.equal(root.querySelectorAll("h1,h2,h3,h4,h5,h6,[role=heading]").length, 0);
    assert.equal(root.querySelectorAll("svg").length, 0);
});

test("no window text is in the page before Buy Now is selected", () => {
    mount();
    assert.equal(isOpen(), false);
    for (const text of [TITLE, DESCRIPTION, BUSINESS, PERSONAL, ...ITEMS[BUSINESS], ...ITEMS[PERSONAL], "Submit", "Cancel", EMPTY]) {
        assert.equal(screen.queryByText(text), null, text);
    }
});

test("Buy Now opens the window with every line word for word, Business Use first", async () => {
    mount();
    await openWindow();
    const text = dialog().textContent ?? "";
    const expected = [
        TITLE,
        DESCRIPTION,
        BUSINESS, ITEMS[BUSINESS][0], EMPTY, ITEMS[BUSINESS][1], EMPTY, "Submit",
        PERSONAL, ITEMS[PERSONAL][0], EMPTY, ITEMS[PERSONAL][1], EMPTY, "Submit",
        "Cancel",
    ];
    // The whole window, in reading order, is exactly these words and nothing else.
    assert.equal(text, expected.join(""));
    for (const line of [BUSINESS, PERSONAL, ...ITEMS[BUSINESS], ...ITEMS[PERSONAL]]) {
        assert.equal(within(dialog()).getAllByText(line).length, 1, line);
    }
    assert.equal(within(dialog()).getAllByRole("button", { name: "Submit" }).length, 2);
    assert.equal(within(dialog()).getAllByRole("button", { name: "Cancel" }).length, 1);
});

test("each upload box comes directly after its own document, inside the right card", async () => {
    mount();
    await openWindow();
    for (const name of [BUSINESS, PERSONAL]) {
        const found = boxes(name);
        assert.equal(found.length, 2, name);
        found.forEach((box, index) => {
            const itemText = box.previousElementSibling as HTMLElement;
            assert.equal(itemText.textContent, ITEMS[name][index]);
            // Tied to its own document for screen readers, with no label text of its own.
            assert.equal(input(box).getAttribute("aria-labelledby"), itemText.id);
            assert.equal(within(box).getByRole("button", { name: EMPTY }).getAttribute("aria-describedby"), itemText.id);
        });
    }
    assert.equal(allBoxes().length, 4);
});

for (const index of [0, 1, 2, 3]) {
    test(`upload box ${index + 1}: choose shows the name, Choose another replaces it, Remove empties it, a dropped file is taken`, async () => {
        mount();
        await openWindow();
        const box = () => allBoxes()[index];
        assert.equal(shownName(box()), null);

        choose(box(), file("first.pdf"));
        assert.equal(shownName(box()), "first.pdf");
        assert.equal(box().querySelector("[data-file-name]")?.getAttribute("title"), "first.pdf");
        assert.equal(within(box()).queryByText(EMPTY), null);

        const opens = pickerOpens(box());
        fireEvent.click(within(box()).getByRole("button", { name: "Choose another" }));
        assert.equal(opens.count, 1, "Choose another opens the file picker");
        choose(box(), file("second.pdf"));
        assert.equal(shownName(box()), "second.pdf");

        fireEvent.click(within(box()).getByRole("button", { name: "Remove" }));
        assert.equal(shownName(box()), null);
        assert.equal(within(box()).getAllByText(EMPTY).length, 1);

        fireEvent.drop(box(), { dataTransfer: { files: [file("dropped.pdf")] } });
        assert.equal(shownName(box()), "dropped.pdf");
        // A drop on a filled box replaces the file, as on the Contacts page.
        fireEvent.drop(box(), { dataTransfer: { files: [file("dropped-again.pdf")] } });
        assert.equal(shownName(box()), "dropped-again.pdf");

        // Only this box changed.
        allBoxes().forEach((other, at) => { if (at !== index) assert.equal(shownName(other), null); });
    });
}

test("each Submit works only when both boxes of its own card have a file", async () => {
    mount();
    await openWindow();
    assert.equal(submit(BUSINESS).disabled, true);
    assert.equal(submit(PERSONAL).disabled, true);

    choose(boxes(BUSINESS)[0], file("registration.pdf"));
    assert.equal(submit(BUSINESS).disabled, true, "one Business file is not enough");
    choose(boxes(BUSINESS)[1], file("id.pdf"));
    assert.equal(submit(BUSINESS).disabled, false);
    assert.equal(submit(PERSONAL).disabled, true, "a full Business card does not enable Personal");

    fireEvent.click(within(boxes(BUSINESS)[0]).getByRole("button", { name: "Remove" }));
    assert.equal(submit(BUSINESS).disabled, true, "removing a file disables it again");

    choose(boxes(PERSONAL)[1], file("id.pdf"));
    assert.equal(submit(PERSONAL).disabled, true, "one Personal file is not enough");
    choose(boxes(PERSONAL)[0], file("bill.pdf"));
    assert.equal(submit(PERSONAL).disabled, false);
    assert.equal(submit(BUSINESS).disabled, true, "a full Personal card does not enable Business");
});

for (const name of [BUSINESS, PERSONAL]) {
    test(`${name}: an enabled Submit closes the window and shows the message word for word`, async () => {
        mount();
        await openWindow();
        boxes(name).forEach((box, index) => choose(box, file(`doc-${index}.pdf`)));
        assert.equal(screen.queryByRole("status", { name: "Success notification" }), null);

        fireEvent.click(submit(name));
        await settle();

        assert.equal(isOpen(), false);
        const toast = screen.getByRole("status", { name: "Success notification" });
        // One message: the whole sentence is the toast's title, with no second line under it.
        const parts = Array.from(toast.querySelectorAll("div")).filter((el) => el.children.length === 0 && (el.textContent ?? "").trim() !== "").map((el) => el.textContent);
        assert.deepEqual(parts, [MESSAGE]);
        // And the title line is not in the one-line, cut-with-an-ellipsis mode.
        const title = Array.from(toast.querySelectorAll("div")).find((el) => el.children.length === 0 && el.textContent === MESSAGE) as HTMLElement;
        assert.equal(title.classList.contains("truncate"), false);
        assert.equal(focusIsOnBuyNow(), true);
    });
}

test("a disabled Submit does nothing", async () => {
    mount();
    await openWindow();
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(isOpen(), true);
    assert.equal(screen.queryByRole("status", { name: "Success notification" }), null);
});

test("Cancel, Escape and a click outside each close the window and return focus to Buy Now", async () => {
    mount();
    const closers: Array<[string, () => void]> = [
        ["Cancel", () => fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }))],
        ["Escape", () => fireEvent.keyDown(window, { key: "Escape" })],
        ["click outside", () => fireEvent.click(dialog().previousElementSibling as HTMLElement)],
    ];
    for (const [name, close] of closers) {
        await openWindow();
        assert.equal(isOpen(), true, name);
        assert.equal(focusIsOnBuyNow(), false, `${name}: focus moved into the window`);
        close();
        await settle();
        assert.equal(isOpen(), false, name);
        assert.equal(focusIsOnBuyNow(), true, name);
        assert.equal(screen.queryByRole("status", { name: "Success notification" }), null, `${name} shows no message`);
    }
});

test("closing the window drops the chosen files", async () => {
    mount();
    await openWindow();
    allBoxes().forEach((box, index) => choose(box, file(`doc-${index}.pdf`)));
    fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
    await settle();
    await openWindow();
    allBoxes().forEach((box) => assert.equal(shownName(box), null));
    assert.equal(submit(BUSINESS).disabled, true);
    assert.equal(submit(PERSONAL).disabled, true);
});

test("Tab reaches Buy Now, every box control, both Submits and Cancel", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    mount();
    await user.tab();
    assert.equal(focusIsOnBuyNow(), true);
    await user.keyboard("{Enter}");
    await settle();
    assert.equal(isOpen(), true, "Enter on Buy Now opens the window");

    const walk = async (stops: number) => {
        dialog().focus();
        const seen: string[] = [];
        for (let i = 0; i < stops; i++) {
            await user.tab();
            const el = document.activeElement as HTMLElement;
            assert.equal(dialog().contains(el), true, "focus stays inside the window");
            seen.push(el.textContent ?? "");
        }
        return seen;
    };
    // Empty: the four boxes and Cancel. A disabled Submit is not a Tab stop, as with every disabled button.
    assert.deepEqual(await walk(5), [EMPTY, EMPTY, EMPTY, EMPTY, "Cancel"]);

    allBoxes().forEach((box, index) => choose(box, file(`doc-${index}.pdf`)));
    assert.deepEqual(await walk(11), [
        "Choose another", "Remove", "Choose another", "Remove", "Submit",
        "Choose another", "Remove", "Choose another", "Remove", "Submit",
        "Cancel",
    ]);
});

test("Enter and Space on an upload box open the file picker", async () => {
    const user = userEvent.setup({ document: globalThis.document });
    mount();
    await openWindow();
    for (const [index, box] of allBoxes().entries()) {
        const opens = pickerOpens(box);
        within(box).getByRole("button", { name: EMPTY }).focus();
        await user.keyboard("{Enter}");
        assert.equal(opens.count, 1, `box ${index + 1}: Enter`);
        await user.keyboard(" ");
        assert.equal(opens.count, 2, `box ${index + 1}: Space`);
    }
    // And on Choose another, once a file is there.
    const first = allBoxes()[0];
    choose(first, file("doc.pdf"));
    const opens = pickerOpens(first);
    within(first).getByRole("button", { name: "Choose another" }).focus();
    await user.keyboard("{Enter}");
    await user.keyboard(" ");
    assert.equal(opens.count, 2);
});

test("choosing, changing, removing, dropping and Submit send no request", async () => {
    mount();
    await openWindow();
    const [a, b, c, d] = allBoxes();
    choose(a, file("one.pdf"));
    choose(a, file("changed.pdf"));
    fireEvent.click(within(a).getByRole("button", { name: "Remove" }));
    fireEvent.drop(a, { dataTransfer: { files: [file("dropped.pdf")] } });
    choose(b, file("two.pdf"));
    fireEvent.drop(c, { dataTransfer: { files: [file("three.pdf")] } });
    choose(d, file("four.pdf"));
    fireEvent.click(submit(BUSINESS));
    await settle();
    await openWindow();
    boxes(PERSONAL).forEach((box, index) => choose(box, file(`p-${index}.pdf`)));
    fireEvent.click(submit(PERSONAL));
    await settle();
    assert.equal(isOpen(), false);
    assert.equal(apiCalls, 0, "api.request");
    assert.equal(fetchCalls, 0, "fetch");
});
