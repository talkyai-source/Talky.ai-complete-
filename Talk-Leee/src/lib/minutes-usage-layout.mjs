function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export const MINUTES_USAGE_LAYOUT_SPEC = Object.freeze({
  dividerPx: 1,
  sidePaddingPx: 12,
  minFontPx: 14,
  maxFontPx: 18,
  charWidthFactor: 0.62,
});

export function computeFittedFontPx({ containerPx, text, minFontPx, maxFontPx, charWidthFactor }) {
  const container = Math.max(0, Number(containerPx) || 0);
  const minFont = Math.max(1, Number(minFontPx) || 1);
  const maxFont = Math.max(minFont, Number(maxFontPx) || minFont);
  const factor = Math.max(0.1, Number(charWidthFactor) || 0.62);
  const rawText = String(text ?? "");
  const len = Math.max(1, rawText.length);

  if (container <= 0) return minFont;

  const ideal = Math.floor(container / (len * factor));
  return clamp(ideal, minFont, maxFont);
}

export function computeMinutesUsageFontPx({ containerPx, usedText, remainingText, spec = MINUTES_USAGE_LAYOUT_SPEC }) {
  const container = Math.max(0, Number(containerPx) || 0);
  const halfPx = Math.max(0, (container - spec.dividerPx) / 2 - spec.sidePaddingPx);

  const usedPx = computeFittedFontPx({
    containerPx: halfPx,
    text: usedText,
    minFontPx: spec.minFontPx,
    maxFontPx: spec.maxFontPx,
    charWidthFactor: spec.charWidthFactor,
  });

  const remainingPx = computeFittedFontPx({
    containerPx: halfPx,
    text: remainingText,
    minFontPx: spec.minFontPx,
    maxFontPx: spec.maxFontPx,
    charWidthFactor: spec.charWidthFactor,
  });

  return { usedPx, remainingPx, halfPx };
}

