import { afterEach, test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render } from "@testing-library/react";
import { AvailabilityLabel, AVAILABILITY_VALUES } from "./availability-label";

afterEach(() => cleanup());

const SIX = ["Available", "Beta", "Limited", "Admin-only", "Selected plans", "Coming soon"] as const;

test("W5-T4 the label accepts exactly the six values", () => {
    assert.deepEqual([...AVAILABILITY_VALUES], [...SIX]);
});

for (const value of SIX) {
    test(`W5-T4 the label renders ${value}`, () => {
        const { container } = render(<AvailabilityLabel value={value} />);
        assert.equal(container.textContent, value);
        assert.equal(container.querySelectorAll("a, button").length, 0, "a label is not interactive");
    });
}

test("W5-T4 any other value is a type error", () => {
    // The directive below fails `tsc` if the prop ever accepts a seventh value.
    // @ts-expect-error "Maybe" is not an availability
    const el = <AvailabilityLabel value="Maybe" />;
    assert.ok(el);
});

// The grey pills keep an opaque light fill in dark mode, where their dark grey text reads well; only
// the translucent emerald, amber and sky pills sit on the black section and need a light text colour.
test("W5-T4 every translucent pill has a dark-mode text colour", () => {
    for (const value of ["Available", "Beta", "Limited", "Selected plans"] as const) {
        const { container } = render(<AvailabilityLabel value={value} />);
        const cls = container.firstElementChild!.getAttribute("class") ?? "";
        assert.match(cls, /dark:text-\S+/, `${value} has no dark:text colour`);
    }
});
