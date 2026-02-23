/**
 * @typedef {Object} DonutTextLayoutInput
 * @property {number} cx Center x in SVG units.
 * @property {number} cy Center y in SVG units.
 * @property {number} startAngleRad Segment start angle in radians.
 * @property {number} sweepAngleRad Segment sweep angle in radians (>= 0).
 * @property {number} rInner Inner radius of the ring segment.
 * @property {number} rOuter Outer radius of the ring segment.
 * @property {number} paddingPx Minimum padding from segment edges.
 * @property {string} text Primary label text.
 * @property {number} minFontPx Minimum readable font size.
 * @property {number} maxFontPx Maximum font size.
 */

/**
 * @typedef {Object} DonutTextLayoutResult
 * @property {boolean} render Whether to render text for this segment.
 * @property {boolean} truncated Whether the text was truncated.
 * @property {number} fontPx Computed font size (px).
 * @property {number} x Text x position.
 * @property {number} y Text y position (baseline).
 * @property {string[]} lines Lines to render (1–2).
 * @property {string} fullText Original full text (for tooltips).
 */

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function approxCharWidthPx(fontPx) {
  return fontPx * 0.62;
}

function splitToTwoLines(text, maxCharsPerLine) {
  const trimmed = String(text ?? "").trim();
  if (trimmed.length <= maxCharsPerLine) return { lines: [trimmed], truncated: false };
  if (maxCharsPerLine <= 3) return { lines: ["…"], truncated: true };

  const first = trimmed.slice(0, maxCharsPerLine);
  const remaining = trimmed.slice(maxCharsPerLine);
  const secondMax = maxCharsPerLine;
  if (remaining.length <= secondMax) return { lines: [first, remaining], truncated: false };
  const second = remaining.slice(0, Math.max(1, secondMax - 1)).trimEnd() + "…";
  return { lines: [first, second], truncated: true };
}

/**
 * Compute mathematically stable text placement inside a donut chart segment.
 * The algorithm uses segment arc-length and ring thickness to determine:
 * - Whether text can be rendered (fallback for very small segments)
 * - A font size scaled by segment area with an 8px minimum
 * - A centered position at the segment's angular midpoint
 * - Up to two wrapped lines with ellipsis truncation
 *
 * @param {DonutTextLayoutInput} input Layout parameters.
 * @returns {DonutTextLayoutResult} Layout result for rendering.
 */
export function computeDonutSegmentTextLayout(input) {
  const cx = Number(input.cx);
  const cy = Number(input.cy);
  const start = Number(input.startAngleRad);
  const sweep = Math.max(0, Number(input.sweepAngleRad));
  const rInner = Math.max(0, Number(input.rInner));
  const rOuter = Math.max(rInner, Number(input.rOuter));
  const padding = Math.max(0, Number(input.paddingPx));
  const minFontPx = Math.max(8, Number(input.minFontPx ?? 8));
  const maxFontPx = Math.max(minFontPx, Number(input.maxFontPx ?? 14));
  const fullText = String(input.text ?? "");

  const thickness = Math.max(0, rOuter - rInner);
  const usableInner = rInner + padding;
  const usableOuter = rOuter - padding;
  const radius = (() => {
    if (usableOuter <= usableInner) return usableInner;
    if (sweep <= 0) return clamp((usableInner + usableOuter) / 2, usableInner, usableOuter);

    const thetaHalf = sweep / 2;
    const k = thetaHalf === 0 ? 0 : Math.sin(thetaHalf) / thetaHalf;
    const ri = usableInner;
    const ro = usableOuter;
    const radialMoment = (ro * ro * ro - ri * ri * ri) / Math.max(1e-9, ro * ro - ri * ri);
    const centroidRadius = (2 / 3) * radialMoment * k;
    return clamp(centroidRadius, usableInner, usableOuter);
  })();

  const area = 0.5 * sweep * Math.max(0, rOuter * rOuter - rInner * rInner);
  const areaScale = Math.sqrt(Math.max(0, area));
  const fontPx = clamp(Math.floor(areaScale * 0.22), minFontPx, Math.min(maxFontPx, Math.floor(thickness * 0.9)));

  const mid = start + sweep / 2;
  const arcLen = sweep * radius;
  const availableWidth = Math.max(0, arcLen - padding * 2);
  const maxChars = Math.floor(availableWidth / approxCharWidthPx(fontPx));

  const tooSmall =
    sweep < (10 * Math.PI) / 180 ||
    thickness < minFontPx + padding ||
    availableWidth < minFontPx * 1.6 ||
    maxChars <= 0;

  const x = cx + radius * Math.cos(mid);
  const y = cy + radius * Math.sin(mid);

  if (tooSmall) {
    return {
      render: true,
      truncated: true,
      fontPx: minFontPx,
      x,
      y,
      lines: ["…"],
      fullText,
    };
  }

  const maxCharsPerLine = Math.max(1, Math.min(maxChars, 14));
  const { lines, truncated } = splitToTwoLines(fullText, maxCharsPerLine);

  return {
    render: true,
    truncated,
    fontPx,
    x,
    y,
    lines,
    fullText,
  };
}
