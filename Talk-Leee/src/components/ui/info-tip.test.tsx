/**
 * InfoTip — rendered, not grepped (goals.md §8).
 *
 * This file replaces one that read info-tip.tsx with readFileSync and matched
 * regexes against the source. That test passed on the *text* of the component,
 * so it would have stayed green if the file had been replaced by a comment
 * containing the right words, and it proved nothing about whether a tip opens,
 * closes, or is announced. §8's acceptance criteria are about behaviour, so
 * these tests mount the component and drive it the way a person would.
 *
 * Its stated excuse — "this repo has no jsdom harness" — was not true: there is
 * one (src/test-utils/dom.ts) and ten other components already use it. What was
 * true, and is the reason a source-regex test looked like the only option, is
 * that `npm test` never ran .tsx files at all. `node --test` with no pattern
 * globs js/mjs/cjs/ts/mts/cts and NOT tsx, so every *.test.tsx in this repo was
 * silently skipped. package.json now passes explicit patterns. If you add a test
 * file with a new extension, check it against that list or it will "pass" by
 * never running.
 *
 * WHAT IS NOT COVERED HERE, AND WHY
 * ---------------------------------
 * §8 asks that "the popup stays inside the viewport". That is a *layout*
 * outcome: Radix measures the trigger and the content with
 * getBoundingClientRect and flips or shifts the panel accordingly. jsdom has no
 * layout engine — every rect is 0x0 and there is no viewport — so flipping and
 * shifting cannot be observed here, and asserting them would be theatre. The
 * test below checks only the part that is real in a DOM: the width cap is
 * actually applied to the rendered node. Whether the browser then honours it
 * belongs in the Playwright suite (npm run test:visual), not here.
 *
 * Hover is likewise absent: Radix opens on pointermove after a delay, which in
 * jsdom means asserting on a timer rather than on the behaviour. Focus and tap
 * are the two paths §8 names as acceptance criteria ("works with mouse and
 * keyboard", "mobile users can open and close"), and both are covered.
 */
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";

import { InfoTip, LabelWithInfo } from "@/components/ui/info-tip";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(() => cleanup());

const TIP_TEXT = "Tokens are pieces of input and output text.";

function renderTip(props: Partial<React.ComponentProps<typeof InfoTip>> = {}) {
    render(
        <InfoTip label="About tokens" {...props}>
            {props.children ?? TIP_TEXT}
        </InfoTip>,
    );
    return screen.getByRole("button", { name: "About tokens" });
}

/** The tip is open when Radix has mounted its content; text appears in the
 *  visible panel and again in the visually-hidden node the trigger points at. */
function openTipNodes() {
    return screen.queryAllByText(TIP_TEXT);
}

// ── the trigger ─────────────────────────────────────────────────────────────

test("the trigger is a real button carrying the label as its accessible name", () => {
    const trigger = renderTip();

    // A <span> with a hover handler is invisible to the keyboard. Asserting on
    // the role means the accessibility tree agrees, not just the tag name.
    assert.equal(trigger.tagName, "BUTTON");
    assert.equal(trigger.getAttribute("type"), "button", "must not submit a surrounding form");
    assert.ok(!trigger.hasAttribute("disabled"));

    // Closed to begin with: nothing is announced and nothing is on screen.
    assert.equal(openTipNodes().length, 0);
    assert.equal(screen.queryByRole("tooltip"), null);
});

test("each trigger gets its own accessible name rather than a shared 'more info'", () => {
    render(
        <div>
            <InfoTip label="About tokens">Tokens explanation</InfoTip>
            <InfoTip label="About creativity">Creativity explanation</InfoTip>
        </div>,
    );

    assert.ok(screen.getByRole("button", { name: "About tokens" }));
    assert.ok(screen.getByRole("button", { name: "About creativity" }));
});

// ── mobile tap: open and close ──────────────────────────────────────────────

test("a tap opens the tip and renders its content", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    await user.click(trigger);

    await waitFor(() => assert.ok(openTipNodes().length > 0, "tip content must render"));
});

test("tapping the trigger again closes the tip", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    await user.click(trigger);
    await waitFor(() => assert.ok(openTipNodes().length > 0));

    await user.click(trigger);
    await waitFor(() => assert.equal(openTipNodes().length, 0, "a second tap must dismiss it"));
});

test("a tap outside dismisses a pinned tip", async () => {
    const user = userEvent.setup();
    render(
        <div>
            <InfoTip label="About tokens">{TIP_TEXT}</InfoTip>
            <button type="button">Somewhere else</button>
        </div>,
    );

    await user.click(screen.getByRole("button", { name: "About tokens" }));
    await waitFor(() => assert.ok(openTipNodes().length > 0));

    // Without this the tip is permanently stuck open on a touch screen, which
    // is the exact failure §8's "mobile users can open and close" rules out.
    fireEvent.pointerDown(screen.getByRole("button", { name: "Somewhere else" }));

    await waitFor(() => assert.equal(openTipNodes().length, 0));
});

// ── keyboard: reachable and dismissible ─────────────────────────────────────

test("the tip is reachable by keyboard — Tab focuses the trigger, Enter opens it", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    await user.tab();
    assert.equal(document.activeElement, trigger, "the trigger must be in the tab order");

    await user.keyboard("{Enter}");
    await waitFor(() => assert.ok(openTipNodes().length > 0, "Enter must open the tip"));
});

test("Space opens the tip too, because the trigger is a button", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    trigger.focus();
    await user.keyboard(" ");

    await waitFor(() => assert.ok(openTipNodes().length > 0));
});

test("Escape dismisses a tip opened from the keyboard", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    trigger.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => assert.ok(openTipNodes().length > 0));

    await user.keyboard("{Escape}");

    // Escape has to clear the pinned state as well as Radix's own open state —
    // otherwise a keyboard user has opened something they cannot close.
    await waitFor(() => assert.equal(openTipNodes().length, 0, "Escape must close the tip"));
});

// ── aria wiring ─────────────────────────────────────────────────────────────

test("an open tip is announced: the trigger describes itself with the tip content", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    assert.equal(trigger.getAttribute("aria-describedby"), null, "nothing to describe while closed");

    await user.click(trigger);

    await waitFor(() => {
        const describedBy = trigger.getAttribute("aria-describedby");
        assert.ok(describedBy, "an open tip must be wired to the trigger");
        const described = document.getElementById(describedBy);
        assert.ok(described, "aria-describedby must point at a node that exists");
        assert.match(described.textContent ?? "", /Tokens are pieces of input and output text\./);
    });

    // Radix marks the announced copy so assistive tech reads it as a tip and
    // not as anonymous body text.
    await waitFor(() => assert.ok(screen.getByRole("tooltip")));
});

test("the trigger reflects its open state for styling and for assistive tech", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    assert.equal(trigger.getAttribute("data-state"), "closed");
    await user.click(trigger);
    await waitFor(() => assert.notEqual(trigger.getAttribute("data-state"), "closed"));
});

// ── content ─────────────────────────────────────────────────────────────────

test("optional Learn more renders as a real link to the given href", async () => {
    const user = userEvent.setup();
    render(
        <InfoTip label="About tokens" learnMoreHref="/docs/tokens" learnMoreLabel="Read the guide">
            {TIP_TEXT}
        </InfoTip>,
    );

    await user.click(screen.getByRole("button", { name: "About tokens" }));

    await waitFor(() => {
        // Radix renders the content twice: the visible panel and the
        // visually-hidden copy that aria-describedby points at. Both must carry
        // the real href, so a screen-reader user is offered the same link.
        const links = screen.getAllByRole("link", { name: "Read the guide" });
        assert.ok(links.length > 0);
        for (const link of links) assert.equal(link.getAttribute("href"), "/docs/tokens");
    });
});

test("no Learn more link when no href is given", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    await user.click(trigger);

    await waitFor(() => assert.ok(openTipNodes().length > 0));
    assert.equal(screen.queryByRole("link"), null);
});

test("rich content is rendered, not stringified", async () => {
    const user = userEvent.setup();
    render(
        <InfoTip label="About tokens">
            <span data-testid="rich">Higher limits allow longer replies.</span>
        </InfoTip>,
    );

    await user.click(screen.getByRole("button", { name: "About tokens" }));

    await waitFor(() => assert.ok(screen.getAllByTestId("rich").length > 0));
});

// ── staying on screen: the part that IS observable without a layout engine ──

test("the panel carries the viewport width cap", async () => {
    const user = userEvent.setup();
    const trigger = renderTip();

    await user.click(trigger);

    await waitFor(() => {
        // Only the cap being applied to the rendered node is checkable here;
        // flip/shift needs a real viewport. See the header note.
        const panel = openTipNodes()
            .map((n) => n.closest("[data-info-tip]"))
            .find((n): n is HTMLElement => n instanceof HTMLElement && n.className.includes("max-w-"));
        assert.ok(panel, "the content panel must cap its width against the viewport");
        assert.match(panel.className, /max-w-\[min\(20rem,calc\(100vw-1\.5rem\)\)\]/);
    });
});

// ── LabelWithInfo ───────────────────────────────────────────────────────────

test("LabelWithInfo shows the label and derives an accessible name from it", () => {
    render(<LabelWithInfo tip="Lower values are more predictable.">Creativity</LabelWithInfo>);

    assert.ok(screen.getByText("Creativity"));
    // "About Creativity", not a ninth button called "more info".
    assert.ok(screen.getByRole("button", { name: "About Creativity" }));
});

test("LabelWithInfo opens the same tip its label describes", async () => {
    const user = userEvent.setup();
    render(<LabelWithInfo tip="Lower values are more predictable.">Creativity</LabelWithInfo>);

    await user.click(screen.getByRole("button", { name: "About Creativity" }));

    await waitFor(() =>
        assert.ok(screen.getAllByText("Lower values are more predictable.").length > 0),
    );
});
