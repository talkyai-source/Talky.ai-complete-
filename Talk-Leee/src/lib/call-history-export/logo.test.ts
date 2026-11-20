import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { BRAND_NAVY, forceLightSvg, isSvgSource, layoutSignature, loadBrandSignaturePng } from "./logo";

const FAVICON = readFileSync(join(process.cwd(), "public", "favicon.svg"), "utf8");
const pathData = (svg: string) => /\sd="([^"]+)"/.exec(svg)?.[1];

test("forceLightSvg drops the dark-mode style, paints the navy and keeps the drawing", () => {
    assert.ok(FAVICON.includes("prefers-color-scheme"), "the real favicon has the media queries");
    const out = forceLightSvg(FAVICON);
    assert.ok(!out.includes("prefers-color-scheme"));
    assert.ok(!/<style/i.test(out));
    assert.ok(out.includes(`fill="${BRAND_NAVY}"`));
    assert.ok(!/#ffffff/i.test(out));
    assert.match(out, /viewBox="327 327 369 369"/);
    assert.match(out, /<svg\b[^>]*\swidth="369"/);
    assert.match(out, /<svg\b[^>]*\sheight="369"/);
    assert.equal((out.match(/<svg\b/g) ?? []).length, 1);
    assert.equal((out.match(/<\/svg>/g) ?? []).length, 1);
    assert.equal(pathData(out), pathData(FAVICON));
});

test("forceLightSvg colours an SVG that has no style block and adds a size from the viewBox", () => {
    const out = forceLightSvg('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><path d="M0 0h10v10z"/></svg>');
    assert.ok(out.includes(`fill="${BRAND_NAVY}"`));
    assert.match(out, /width="40"/);
    assert.match(out, /height="20"/);
    assert.match(out, /viewBox="0 0 40 20"/);
    assert.equal(pathData(out), "M0 0h10v10z");
});

test("forceLightSvg returns a non-SVG input unchanged", () => {
    assert.equal(forceLightSvg("not an svg at all"), "not an svg at all");
});

test("isSvgSource tells an SVG from a partner PNG", () => {
    assert.equal(isSvgSource("/favicon.svg"), true);
    assert.equal(isSvgSource("/logo.svg?v=2"), true);
    assert.equal(isSvgSource("/partner.png"), false);
});

test("layoutSignature keeps the navbar's proportions", () => {
    const l = layoutSignature(288, 1, (fontPx) => fontPx * 4);
    assert.equal(l.pad, 12);
    assert.equal(l.emblemHeight, 264);
    assert.equal(l.emblemWidth, 264);
    assert.ok(Math.abs(l.fontPx - 264 / 1.55) < 1e-9);
    assert.equal(l.gap, Math.round(264 * 0.3));
    assert.equal(l.textX, 12 + 264 + l.gap);
    assert.equal(l.textY, 144);
    assert.equal(l.canvasHeight, 288);
    assert.equal(l.canvasWidth, Math.ceil(l.textX + l.fontPx * 4 + 12));
});

test("layoutSignature widens the emblem for a wide partner logo", () => {
    const l = layoutSignature(288, 2, () => 100);
    assert.equal(l.emblemWidth, 528);
    assert.equal(l.textX, 12 + 528 + l.gap);
});

function withDocument<T>(value: unknown, run: () => Promise<T>): Promise<T> {
    const g = globalThis as unknown as Record<string, unknown>;
    const original = Object.getOwnPropertyDescriptor(globalThis, "document");
    Object.defineProperty(globalThis, "document", { value, configurable: true, writable: true });
    return run().finally(() => {
        if (original) Object.defineProperty(globalThis, "document", original);
        else delete g.document;
    });
}

test("loadBrandSignaturePng returns null when there is no document", async () => {
    const result = await withDocument(undefined, () => loadBrandSignaturePng({ name: "Talk-Lee", logoSrc: "/favicon.svg" }));
    assert.equal(result, null);
});

test("loadBrandSignaturePng never throws when the emblem fetch rejects", async () => {
    const g = globalThis as unknown as Record<string, unknown>;
    const hadImage = "Image" in g;
    g.Image = class { };
    let fetched = "";
    try {
        const result = await withDocument({}, () => loadBrandSignaturePng(
            { name: "Talk-Lee", logoSrc: "/favicon.svg" },
            { fetchFn: async (src: string) => { fetched = src; throw new Error("offline"); } },
        ));
        assert.equal(result, null);
        assert.equal(fetched, "/favicon.svg");
    } finally {
        if (!hadImage) delete g.Image;
    }
});
