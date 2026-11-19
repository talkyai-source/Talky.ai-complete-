import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, render } from "@testing-library/react";

import { CallerGave, NOT_PROVIDED_TEXT } from "./caller-gave";

afterEach(() => cleanup());
const dds = (container: HTMLElement) => Array.from(container.querySelectorAll("dd")).map((el) => el.textContent);

test("both given: email then number, each in its own pair", () => {
    const { container } = render(<CallerGave variant="row" call={{ captured_email: "a@b.co", captured_phone: "+923085397539" }} />);
    assert.deepEqual(Array.from(container.querySelectorAll("dt")).map((el) => el.textContent), ["Email", "Number"]);
    assert.deepEqual(dds(container), ["a@b.co", "+92 308 5397539"]);
    assert.equal((container.textContent ?? "").includes("Caller gave"), true);
    assert.equal((container.textContent ?? "").includes(NOT_PROVIDED_TEXT), false);
});

test("only one given: the other says Not provided", () => {
    const { container } = render(<CallerGave variant="card" call={{ captured_email: "a@b.co", captured_phone: null }} />);
    assert.deepEqual(dds(container), ["a@b.co", "Not provided"]);
});

test("none given: Not provided twice", () => {
    const { container } = render(<CallerGave variant="tile" call={{ captured_email: null, captured_phone: null }} />);
    assert.deepEqual(dds(container), ["Not provided", "Not provided"]);
});

test("an unconfirmed value is marked, a confirmed one is not", () => {
    const { container } = render(
        <CallerGave variant="row" call={{ captured_email: "a@b.co", captured_email_confirmed: false, captured_phone: "+923085397539", captured_phone_confirmed: true }} />,
    );
    const [email, phone] = Array.from(container.querySelectorAll("dd"));
    assert.equal(email.textContent, "a@b.co (unconfirmed)");
    assert.equal((email.querySelector("span")?.getAttribute("title") ?? "").includes("did not confirm"), true);
    assert.equal((phone.textContent ?? "").includes("unconfirmed"), false);
});

test("every variant carries data-caller-gave", () => {
    for (const variant of ["row", "card", "tile"] as const) {
        const { container, unmount } = render(<CallerGave variant={variant} call={{}} />);
        assert.equal(container.querySelector(`[data-caller-gave="${variant}"]`) !== null, true, variant);
        unmount();
    }
});
