/**
 * Settings → Telephony: the "Buy Now" card, its documents window, the four
 * upload boxes and the two Submit buttons. Choosing files sends nothing; Submit
 * sends exactly one POST (fetch is faked here: no real network).
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
type Seen = { url: string; method: string; headers: Headers; body: BodyInit | null | undefined };
let apiCalls = 0;
let seen: Seen[] = [];
const writes = () => seen.filter((call) => call.method !== "GET");
const CREATED = { id: "11111111-1111-1111-1111-111111111111", use_type: "business", status: "submitted", submitted_at: "2026-11-25T10:00:00Z", documents: [] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const defaultConfig = async () => json({ accepted_types: ["application/pdf", "image/jpeg", "image/png"], accepted_extensions: [".pdf", ".jpg", ".jpeg", ".png"], max_file_bytes: 10485760 });
const created = async () => json(CREATED, 201);
let configAnswer: () => Promise<Response> = defaultConfig;
let postAnswer: () => Promise<Response> = created;

beforeEach(() => {
    apiCalls = 0;
    seen = [];
    configAnswer = defaultConfig;
    postAnswer = created;
    api.request = (async () => { apiCalls += 1; return {}; }) as typeof api.request;
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
        const call = { url: String(input), method: init?.method ?? "GET", headers: new Headers(init?.headers), body: init?.body };
        seen.push(call);
        return call.method === "GET" ? configAnswer() : postAnswer();
    }) as typeof fetch;
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
        // Sentry records an element's title, so none is set; the name is the visible text only.
        assert.equal(box().querySelector("[data-file-name]")?.hasAttribute("title"), false);
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

        assert.equal(writes().length, 1, "one POST");
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

test("choosing, changing, removing and dropping files send no write request; each Submit sends exactly one", async () => {
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
    await settle();
    assert.equal(writes().length, 0, "no write before Submit");
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(writes().length, 1);
    await openWindow();
    boxes(PERSONAL).forEach((box, index) => choose(box, file(`p-${index}.pdf`)));
    fireEvent.click(submit(PERSONAL));
    await settle();
    assert.equal(isOpen(), false);
    assert.equal(writes().length, 2);
    assert.equal(apiCalls, 0, "api.request");
});

const fill = (name: string) => boxes(name).forEach((box, index) => choose(box, file(`doc-${index}.pdf`)));
const sentForm = (at: number) => writes()[at].body as FormData;
const failWith = (status: number, message: string) => () => Promise.resolve(json({ error: { code: "x", message, details: {}, request_id: "r" } }, status));
const toastShown = () => screen.queryByRole("status", { name: "Success notification" }) !== null;

test("Business Submit sends use_type first, the two business fields and a key", async () => {
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(writes().length, 1);
    assert.deepEqual(Array.from(sentForm(0).keys()), ["use_type", "business_registration", "business_photo_id"]);
    assert.equal(sentForm(0).get("use_type"), "business");
    assert.match(writes()[0].headers.get("Idempotency-Key") ?? "", /^[A-Za-z0-9_-]{8,128}$/);
});

test("Personal Submit sends use_type first and the two personal fields", async () => {
    mount();
    await openWindow();
    fill(PERSONAL);
    fireEvent.click(submit(PERSONAL));
    await settle();
    assert.deepEqual(Array.from(sentForm(0).keys()), ["use_type", "utility_bill", "personal_photo_id"]);
    assert.equal(sentForm(0).get("use_type"), "personal");
});

test("a server refusal keeps the files, shows the server's message in that card and no success message", async () => {
    postAnswer = failWith(413, "Business registration is larger than 10 MB.");
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(isOpen(), true);
    assert.equal(toastShown(), false);
    assert.equal(card(BUSINESS).querySelector("[data-submit-error]")?.textContent, "Business registration is larger than 10 MB.");
    assert.equal(card(PERSONAL).querySelector("[data-submit-error]") === null, true);
    assert.equal(shownName(boxes(BUSINESS)[0]), "doc-0.pdf");
    assert.equal(shownName(boxes(BUSINESS)[1]), "doc-1.pdf");
    assert.equal(submit(BUSINESS).disabled, false);
});

test("a retry with the same files re-uses the key; a changed file makes a new key; success clears it", async () => {
    postAnswer = failWith(503, "Your documents could not be saved. Nothing was kept. Please try again.");
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    fireEvent.click(submit(BUSINESS));
    await settle();
    const keys = () => writes().map((call) => call.headers.get("Idempotency-Key"));
    assert.equal(keys()[0], keys()[1], "same files, same key");
    choose(boxes(BUSINESS)[0], file("replaced.pdf"));
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.notEqual(keys()[2], keys()[1], "a changed file makes a new key");
    postAnswer = created;
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(keys()[3], keys()[2]);
    assert.equal(isOpen(), false);
    assert.equal(toastShown(), true);
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.notEqual(keys()[4], keys()[3], "the key was cleared on success");
});

for (const [status, code, message] of [
    [429, "too_many_open_requests", "Your earlier requests are still waiting for review. Please wait for a decision before sending more documents."],
    [503, "save_unconfirmed", "We could not confirm that your documents were saved. Please send the same files again."],
    [422, "invalid_form", "Send Business registration as a file."],
] as const) {
    test(`${code} (${status}): the server's message shows in the card as sent, the files stay, and a second Submit re-uses the same key`, async () => {
        postAnswer = () => Promise.resolve(json({ error: { code, message, details: {}, request_id: "r" } }, status));
        mount();
        await openWindow();
        fill(BUSINESS);
        fireEvent.click(submit(BUSINESS));
        await settle();
        assert.equal(isOpen(), true);
        assert.equal(toastShown(), false);
        assert.equal(card(BUSINESS).querySelector("[data-submit-error]")?.textContent, message);
        assert.equal(shownName(boxes(BUSINESS)[0]), "doc-0.pdf");
        assert.equal(shownName(boxes(BUSINESS)[1]), "doc-1.pdf");
        fireEvent.click(submit(BUSINESS));
        await settle();
        assert.equal(writes().length, 2);
        assert.equal(writes()[1].headers.get("Idempotency-Key"), writes()[0].headers.get("Idempotency-Key"));
    });
}

test("while sending: both Submits and Cancel are disabled, aria-busy is set, the window cannot be closed, no message yet", async () => {
    let release: () => void = () => {};
    postAnswer = () => new Promise<Response>((resolve) => { release = () => resolve(json(CREATED, 201)); });
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(submit(BUSINESS).disabled, true);
    assert.equal(submit(PERSONAL).disabled, true);
    assert.equal((within(dialog()).getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled, true);
    assert.equal(dialog().querySelector("[data-document-grid]")?.getAttribute("aria-busy"), "true");
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(dialog().previousElementSibling as HTMLElement);
    await settle();
    assert.equal(isOpen(), true, "cannot be closed while sending");
    assert.equal(toastShown(), false, "no message before the server answered");
    assert.equal(writes().length, 1);
    await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 10)); });
    assert.equal(isOpen(), false);
    assert.equal(toastShown(), true);
});

test("a file the browser refuses is not taken, shows the server's sentence and sends nothing", async () => {
    mount();
    await openWindow();
    const box = boxes(BUSINESS)[0];
    choose(box, new File(["x"], "notes.txt", { type: "text/plain" }));
    assert.equal(shownName(box), null);
    assert.equal(card(BUSINESS).querySelector("[data-file-error]")?.textContent, "Business registration must be a PDF, JPG or PNG file.");
    choose(box, file("ok.pdf"));
    assert.equal(card(BUSINESS).querySelector("[data-file-error]") === null, true);
    assert.equal(shownName(box), "ok.pdf");
    assert.equal(writes().length, 0);
});

test("the limits come from the server when the window opens; the defaults stand if that fails", async () => {
    configAnswer = async () => json({ accepted_types: ["application/pdf"], accepted_extensions: [".pdf"], max_file_bytes: 2097152 });
    mount();
    await openWindow();
    assert.equal(seen.filter((call) => call.method === "GET").length, 1);
    assert.equal(input(allBoxes()[0]).getAttribute("accept"), ".pdf,application/pdf");
    choose(allBoxes()[0], new File(["x"], "a.png", { type: "image/png" }));
    assert.equal(card(BUSINESS).querySelector("[data-file-error]")?.textContent, "Business registration must be a PDF, JPG or PNG file.");
    cleanup();

    seen = [];
    configAnswer = () => Promise.reject(new Error("down"));
    mount();
    await openWindow();
    assert.equal(input(allBoxes()[0]).getAttribute("accept"), ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png");
});

// ───────── review fixes ─────────
const stable = (name: string, type = "application/pdf") => new File(["x"], name, { type, lastModified: 1_700_000_000_000 });
const fillStable = (name: string) => boxes(name).forEach((box, index) => choose(box, stable(`stable-${index}.pdf`)));
const keysSent = () => writes().map((call) => call.headers.get("Idempotency-Key"));
const UNSURE = "We could not confirm that your documents were saved. Please send the same files again.";
const NOT_SAVED = "Your documents could not be saved. Nothing was kept. Please try again.";
const cancelWindow = async () => { fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" })); await settle(); };

test("a chosen file's name is the visible text only: Sentry's label is set and no attribute carries the name", async () => {
    mount();
    await openWindow();
    const box = boxes(BUSINESS)[0];
    choose(box, file("jane-doe-passport.pdf"));
    const el = box.querySelector("[data-file-name]") as HTMLElement;
    assert.equal(el.textContent, "jane-doe-passport.pdf");
    assert.equal(el.getAttribute("data-sentry-element"), "number-request-file");
    for (const holder of [el, box]) {
        for (const attr of Array.from(holder.attributes)) assert.equal(attr.value.includes("jane-doe-passport"), false, `${attr.name} carries the file name`);
    }
});

test("after an unsure failure, closing and choosing the same two files again re-uses the key; a changed file makes a new one", async () => {
    postAnswer = failWith(503, UNSURE);
    mount();
    await openWindow();
    fillStable(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    await cancelWindow();
    await openWindow();
    fillStable(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(keysSent()[1], keysSent()[0], "same documents chosen again, same key");
    choose(boxes(BUSINESS)[0], stable("other.pdf"));
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.notEqual(keysSent()[2], keysSent()[1], "a changed file, a new key");
});

test("Business fails, Personal succeeds, Business retried with the same files: Business's own key; a success clears that card's key", async () => {
    postAnswer = failWith(503, UNSURE);
    mount();
    await openWindow();
    fillStable(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    fillStable(PERSONAL);
    postAnswer = created;
    fireEvent.click(submit(PERSONAL));
    await settle();
    assert.equal(isOpen(), false);
    await openWindow();
    fillStable(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(keysSent()[2], keysSent()[0], "Business's original key");
    assert.notEqual(keysSent()[1], keysSent()[0], "Personal has its own key");
    assert.equal(isOpen(), false, "Business now succeeded");
    await openWindow();
    fillStable(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.notEqual(keysSent()[3], keysSent()[0], "success cleared Business's key");
});

test("after a failed Submit, keyboard focus is on that card's message", async () => {
    postAnswer = failWith(503, UNSURE);
    mount();
    await openWindow();
    fill(BUSINESS);
    submit(BUSINESS).focus();
    fireEvent.click(submit(BUSINESS));
    await settle();
    const message = card(BUSINESS).querySelector("[data-submit-error]") as HTMLElement;
    assert.equal(message.getAttribute("role"), "alert");
    assert.equal(message.getAttribute("tabindex"), "-1");
    assert.equal(document.activeElement === message, true);
});

test("while sending, every upload control on both cards is locked and a drop is ignored", async () => {
    let release: () => void = () => {};
    postAnswer = () => new Promise<Response>((resolve) => { release = () => resolve(json(CREATED, 201)); });
    mount();
    await openWindow();
    fill(BUSINESS);
    choose(boxes(PERSONAL)[0], file("p.pdf"));
    fireEvent.click(submit(BUSINESS));
    await settle();
    for (const box of allBoxes()) {
        assert.equal(input(box).disabled, true);
        for (const button of Array.from(box.querySelectorAll("button"))) assert.equal(button.disabled, true, button.textContent ?? "");
    }
    fireEvent.drop(boxes(PERSONAL)[1], { dataTransfer: { files: [file("late.pdf")] } });
    fireEvent.drop(boxes(PERSONAL)[0], { dataTransfer: { files: [file("swap.pdf")] } });
    assert.equal(shownName(boxes(PERSONAL)[1]), null);
    assert.equal(shownName(boxes(PERSONAL)[0]), "p.pdf");
    await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 10)); });
});

test("a 201 with a body of an unexpected shape is still a success", async () => {
    postAnswer = async () => json({ unexpected: "shape" }, 201);
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(isOpen(), false);
    assert.equal(toastShown(), true);
});

test("a throw while making the key does not lock the window: it shows the server's could-not-be-saved sentence", async () => {
    const real = Object.getOwnPropertyDescriptor(globalThis.crypto, "randomUUID");
    Object.defineProperty(globalThis.crypto, "randomUUID", { configurable: true, value: () => { throw new Error("no randomness"); } });
    try {
        mount();
        await openWindow();
        fill(BUSINESS);
        fireEvent.click(submit(BUSINESS));
        await settle();
        assert.equal(card(BUSINESS).querySelector("[data-submit-error]")?.textContent, NOT_SAVED);
        assert.equal(submit(BUSINESS).disabled, false);
        assert.equal((within(dialog()).getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled, false);
        assert.equal(writes().length, 0);
        fireEvent.click(within(dialog()).getByRole("button", { name: "Cancel" }));
        await settle();
        assert.equal(isOpen(), false, "the window can be closed");
    } finally {
        if (real) Object.defineProperty(globalThis.crypto, "randomUUID", real);
        else delete (globalThis.crypto as unknown as Record<string, unknown>).randomUUID;
    }
});

for (const [name, answerWith] of [
    ["a timeout", () => Promise.reject(new Error("Timeout"))],
    ["a network failure", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["a gateway error with no server envelope", async () => new Response("<html>Bad gateway</html>", { status: 502, headers: { "content-type": "text/html" } })],
] as const) {
    test(`${name}: the customer is told the server's could-not-confirm sentence, the files stay and the same key is re-used`, async () => {
        postAnswer = answerWith;
        mount();
        await openWindow();
        fill(BUSINESS);
        fireEvent.click(submit(BUSINESS));
        await settle();
        assert.equal(card(BUSINESS).querySelector("[data-submit-error]")?.textContent, UNSURE);
        assert.equal(shownName(boxes(BUSINESS)[0]), "doc-0.pdf");
        fireEvent.click(submit(BUSINESS));
        await settle();
        assert.equal(keysSent()[1], keysSent()[0]);
    });
}

test("an answer for an already-decided request: the key is forgotten and ONE more send goes out with a new key", async () => {
    let n = 0;
    postAnswer = async () => { n += 1; return json(n === 1 ? { ...CREATED, status: "rejected" } : CREATED, 201); };
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(writes().length, 2);
    assert.notEqual(keysSent()[1], keysSent()[0], "a new key");
    assert.equal(isOpen(), false);
    assert.equal(toastShown(), true);
});

test("the second send after a decided request fails: its message shows and the files stay", async () => {
    let n = 0;
    postAnswer = async () => {
        n += 1;
        return n === 1 ? json({ ...CREATED, status: "approved" }, 201) : json({ error: { code: "too_many_open_requests", message: "Your earlier requests are still waiting for review. Please wait for a decision before sending more documents.", details: {}, request_id: "r" } }, 429);
    };
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(writes().length, 2, "exactly one more send, no loop");
    assert.equal(isOpen(), true);
    assert.equal(toastShown(), false);
    assert.equal(card(BUSINESS).querySelector("[data-submit-error]")?.textContent, "Your earlier requests are still waiting for review. Please wait for a decision before sending more documents.");
    assert.equal(shownName(boxes(BUSINESS)[0]), "doc-0.pdf");
});

test("a new request answered 'submitted' is exactly one POST", async () => {
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(writes().length, 1);
    assert.equal(isOpen(), false);
});

test("a decided answer twice is not looped: two POSTs at most", async () => {
    postAnswer = async () => json({ ...CREATED, status: "rejected" }, 201);
    mount();
    await openWindow();
    fill(BUSINESS);
    fireEvent.click(submit(BUSINESS));
    await settle();
    assert.equal(writes().length, 2);
});
