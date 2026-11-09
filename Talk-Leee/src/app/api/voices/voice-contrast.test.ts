import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

// QA F1-08 (QA-UI-160): axe found text below 4.5:1 on /ai-voices. The voice
// initials are drawn from this route's accent pairs; the 600 shades sat on
// their 50 tints at about 3.1:1 (amber) and 3.5:1 (emerald). Every pair the
// route serves is measured here against the real Tailwind palette.
const route = readFileSync(path.join(process.cwd(), "src/app/api/voices/route.ts"), "utf8");
const theme = readFileSync(path.join(process.cwd(), "node_modules/tailwindcss/theme.css"), "utf8");

/** Relative luminance (WCAG) of a Tailwind colour, from its OKLCH definition. */
function luminance(name: string): number {
    if (name === "white") return 1;
    const match = theme.match(new RegExp(`--color-${name}: oklch\\(([\\d.]+)% ([\\d.]+) ([\\d.]+)\\)`));
    assert.ok(match, `no palette entry for ${name}`);
    const [L, C, h] = [Number(match[1]) / 100, Number(match[2]), (Number(match[3]) * Math.PI) / 180];
    const a = C * Math.cos(h);
    const b = C * Math.sin(h);
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    const clip = (v: number) => Math.min(1, Math.max(0, v));
    const R = clip(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s);
    const G = clip(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s);
    const B = clip(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s);
    return 0.2126 * R + 0.7152 * G + 0.0722 * B;
}
function contrast(text: string, background: string) {
    const [x, y] = [luminance(text), luminance(background)];
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const pairs = [...route.matchAll(/color: "text-([a-z]+-\d+)",?\s*bg: "bg-([a-z]+(?:-\d+)?)"/g)].map(([, text, background]) => ({ text, background }));

test("the measurement catches the shade that failed", () => {
    assert.ok(contrast("amber-600", "amber-50") < 4.5);
});

test("every voice initial the route serves meets 4.5:1 on its tint", () => {
    assert.ok(pairs.length >= 5, `found only ${pairs.length} colour pairs`);
    for (const { text, background } of pairs) {
        const ratio = contrast(text, background);
        assert.ok(ratio >= 4.5, `text-${text} on bg-${background} is ${ratio.toFixed(2)}:1`);
    }
});
