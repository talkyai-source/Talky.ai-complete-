/**
 * Number boxes keep what the user typed as text and only turn it into a number
 * on blur and on save, so clearing the box to retype does not snap the value back.
 */
export function clampIntText(text: string, min: number, max: number, fallback: number): number {
    const n = Number.parseInt(text, 10);
    if (!Number.isFinite(n)) return fallback;
    return Math.max(min, Math.min(max, n));
}
