import test from "node:test";
import assert from "node:assert/strict";
import { computeFittedFontPx, computeMinutesUsageFontPx, MINUTES_USAGE_LAYOUT_SPEC } from "../src/lib/minutes-usage-layout.mjs";

function estimateTextWidthPx({ text, fontPx, charWidthFactor }) {
  const len = Math.max(1, String(text ?? "").length);
  return len * charWidthFactor * fontPx;
}

test("computeFittedFontPx stays within bounds and fits container", () => {
  const spec = MINUTES_USAGE_LAYOUT_SPEC;
  const containerPx = 120;
  const text = "123,456";
  const fontPx = computeFittedFontPx({
    containerPx,
    text,
    minFontPx: spec.minFontPx,
    maxFontPx: spec.maxFontPx,
    charWidthFactor: spec.charWidthFactor,
  });

  assert.ok(fontPx >= spec.minFontPx);
  assert.ok(fontPx <= spec.maxFontPx);

  const width = estimateTextWidthPx({ text, fontPx, charWidthFactor: spec.charWidthFactor });
  assert.ok(width <= containerPx);
});

test("computeFittedFontPx decreases with longer text", () => {
  const spec = MINUTES_USAGE_LAYOUT_SPEC;
  const containerPx = 120;
  const short = computeFittedFontPx({
    containerPx,
    text: "999",
    minFontPx: spec.minFontPx,
    maxFontPx: spec.maxFontPx,
    charWidthFactor: spec.charWidthFactor,
  });
  const long = computeFittedFontPx({
    containerPx,
    text: "9,999,999",
    minFontPx: spec.minFontPx,
    maxFontPx: spec.maxFontPx,
    charWidthFactor: spec.charWidthFactor,
  });

  assert.ok(long <= short);
});

test("computeMinutesUsageFontPx fits both halves across value ranges", () => {
  const spec = MINUTES_USAGE_LAYOUT_SPEC;
  const containerPx = 260;
  const cases = [
    { usedText: "0", remainingText: "30,000" },
    { usedText: "12,345", remainingText: "6,789" },
    { usedText: "1,234,567", remainingText: "9,876,543" },
  ];

  for (const { usedText, remainingText } of cases) {
    const { usedPx, remainingPx, halfPx } = computeMinutesUsageFontPx({ containerPx, usedText, remainingText, spec });
    assert.ok(Number.isFinite(usedPx));
    assert.ok(Number.isFinite(remainingPx));
    assert.ok(usedPx >= spec.minFontPx && usedPx <= spec.maxFontPx);
    assert.ok(remainingPx >= spec.minFontPx && remainingPx <= spec.maxFontPx);

    const usedWidth = estimateTextWidthPx({ text: usedText, fontPx: usedPx, charWidthFactor: spec.charWidthFactor });
    const remainingWidth = estimateTextWidthPx({
      text: remainingText,
      fontPx: remainingPx,
      charWidthFactor: spec.charWidthFactor,
    });
    assert.ok(usedWidth <= halfPx);
    assert.ok(remainingWidth <= halfPx);
  }
});

