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
