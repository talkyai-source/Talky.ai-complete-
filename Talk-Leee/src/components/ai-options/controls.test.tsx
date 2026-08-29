import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";

import { RadialKnob } from "./controls";

describe("RadialKnob", () => {
    afterEach(cleanup);

    test("exposes its formatted value and purpose to assistive technology", () => {
        render(
            <RadialKnob
                label="Temp"
                value={0.5}
                min={0}
                max={2}
                step={0.1}
                format={(value) => value.toFixed(1)}
                hint="creativity"
                onChange={() => undefined}
            />,
        );

        const slider = screen.getByRole("slider", { name: "Temp" });
        assert.equal(slider.getAttribute("aria-valuenow"), "0.5");
        assert.equal(slider.getAttribute("aria-valuetext"), "0.5 (creativity)");
    });

    test("supports stepped keyboard changes", () => {
        let nextValue: number | undefined;
        render(
            <RadialKnob
                label="Tokens"
                value={350}
                min={50}
                max={5000}
                step={50}
                hint="max length"
                onChange={(value) => { nextValue = value; }}
            />,
        );

        fireEvent.keyDown(screen.getByRole("slider", { name: "Tokens" }), { key: "ArrowRight" });
        assert.equal(nextValue, 400);
    });

    test("can shrink below its desktop size and retains a visible focus treatment", () => {
        render(
            <RadialKnob
                label="Temp"
                value={0.5}
                min={0}
                max={2}
                step={0.1}
                onChange={() => undefined}
            />,
        );

        const slider = screen.getByRole("slider", { name: "Temp" });
        assert.match(slider.getAttribute("class") ?? "", /\bw-full\b/);
        assert.match(slider.getAttribute("class") ?? "", /\bfocus-visible:ring-2\b/);
        assert.match(slider.getAttribute("style") ?? "", /max-width:\s*112px/);
    });
});
