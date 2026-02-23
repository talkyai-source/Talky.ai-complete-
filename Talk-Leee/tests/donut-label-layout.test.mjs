import test from "node:test";
import assert from "node:assert/strict";
import { computeDonutSegmentTextLayout } from "../src/lib/donut-label-layout.mjs";

test("renders ellipsis fallback for extremely small segment", () => {
  const out = computeDonutSegmentTextLayout({
    cx: 66,
    cy: 66,
    startAngleRad: -Math.PI / 2,
    sweepAngleRad: (4 * Math.PI) / 180,
    rInner: 52,
    rOuter: 66,
    paddingPx: 5,
    text: "Answered 12,345",
    minFontPx: 8,
    maxFontPx: 12,
  });

  assert.equal(out.render, true);
  assert.equal(out.lines.length, 1);
  assert.equal(out.lines[0], "…");
  assert.equal(out.truncated, true);
  assert.equal(out.fontPx, 8);
});

test("scales font size with segment area within bounds", () => {
  const small = computeDonutSegmentTextLayout({
    cx: 66,
    cy: 66,
    startAngleRad: -Math.PI / 2,
    sweepAngleRad: (30 * Math.PI) / 180,
    rInner: 52,
    rOuter: 66,
    paddingPx: 5,
    text: "Failed 123",
    minFontPx: 8,
    maxFontPx: 12,
  });

  const big = computeDonutSegmentTextLayout({
    cx: 66,
    cy: 66,
    startAngleRad: -Math.PI / 2,
    sweepAngleRad: Math.PI,
    rInner: 52,
    rOuter: 66,
    paddingPx: 5,
    text: "Failed 123",
    minFontPx: 8,
    maxFontPx: 12,
  });

  assert.ok(small.fontPx >= 8 && small.fontPx <= 12);
  assert.ok(big.fontPx >= 8 && big.fontPx <= 12);
  assert.ok(big.fontPx >= small.fontPx);
});

test("wraps to two lines and truncates with ellipsis when space is limited", () => {
  const out = computeDonutSegmentTextLayout({
    cx: 66,
    cy: 66,
    startAngleRad: -Math.PI / 2,
    sweepAngleRad: (40 * Math.PI) / 180,
    rInner: 52,
    rOuter: 66,
    paddingPx: 5,
    text: "Answered 123,456,789 calls",
    minFontPx: 8,
    maxFontPx: 12,
  });

  assert.equal(out.render, true);
  assert.ok(out.lines.length >= 1 && out.lines.length <= 2);
  if (out.lines.length === 2) {
    assert.ok(out.lines[1].includes("…"));
    assert.equal(out.truncated, true);
  }
});

test("produces stable snapshot for a typical segment", () => {
  const out = computeDonutSegmentTextLayout({
    cx: 66,
    cy: 66,
    startAngleRad: -Math.PI / 2,
    sweepAngleRad: (220 * Math.PI) / 180,
    rInner: 52,
    rOuter: 66,
    paddingPx: 5,
    text: "Answered 2,345",
    minFontPx: 8,
    maxFontPx: 12,
  });

  assert.deepStrictEqual(
    {
      render: out.render,
      truncated: out.truncated,
      fontPx: out.fontPx,
      lines: out.lines,
      x: Math.round(out.x * 100) / 100,
      y: Math.round(out.y * 100) / 100,
    },
    {
      render: true,
      truncated: false,
      fontPx: 12,
      lines: ["Answered 2,345"],
      x: 119.56,
      y: 85.5,
    }
  );
});
