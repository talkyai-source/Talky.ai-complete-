import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BillingPeriod } from "./billing-period";
import { ensureDom } from "@/test-utils/dom";

ensureDom();
afterEach(() => cleanup());

// The pricing cards hide the unselected price block with CSS keyed on this
// data attribute. Both blocks must always be in the markup, annual must be the
// first-paint state, and the control must be a real switch that reports it.
test("BillingPeriod starts on annual and exposes the period as a data attribute", () => {
  render(
    <BillingPeriod>
      <p>child</p>
    </BillingPeriod>,
  );
  const sw = screen.getByRole("switch", { name: "Annual" });
  assert.equal(sw.getAttribute("aria-checked"), "true");
  const wrapper = screen.getByText("child").closest("[data-billing]");
  assert.ok(wrapper);
  assert.equal(wrapper.getAttribute("data-billing"), "annual");
});

test("BillingPeriod switches to monthly and back without removing children", () => {
  render(
    <BillingPeriod>
      <p>child</p>
    </BillingPeriod>,
  );
  const sw = screen.getByRole("switch", { name: "Annual" });
  const wrapper = screen.getByText("child").closest("[data-billing]")!;

  fireEvent.click(sw);
  assert.equal(sw.getAttribute("aria-checked"), "false");
  assert.equal(wrapper.getAttribute("data-billing"), "monthly");
  assert.ok(screen.getByText("child"));

  fireEvent.click(sw);
  assert.equal(sw.getAttribute("aria-checked"), "true");
  assert.equal(wrapper.getAttribute("data-billing"), "annual");
});
