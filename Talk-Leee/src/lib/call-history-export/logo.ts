import type { CallExportBrand, CallExportLogo } from "./columns";

const LOAD_TIMEOUT_MS = 5000;
/** The site's dark navy, the emblem's light-mode colour (public/favicon.svg). */
export const BRAND_NAVY = "#131d39";
const FALLBACK_FAMILY = "Manrope, ui-sans-serif, system-ui, sans-serif";

type FetchFn = (input: string) => Promise<{ ok: boolean; text: () => Promise<string> }>;

export function isSvgSource(src: string): boolean {
    if (/^data:image\/svg\+xml/i.test(src)) return true;
    return /\.svg$/i.test(src.split(/[?#]/)[0] ?? "");
}

/**
 * Make an SVG independent of the viewer's colour scheme: the <style> block with its
 * prefers-color-scheme rules is dropped and the whole drawing is painted navy; an
 * explicit width and height are guaranteed. Anything that is not an SVG is returned unchanged.
 */
export function forceLightSvg(svgText: string): string {
    const rootMatch = /<svg\b[^>]*>/i.exec(svgText);
    if (!rootMatch) return svgText;
    let root = rootMatch[0];
    const viewBox = /\bviewBox\s*=\s*["']\s*([-\d.]+)[\s,]+([-\d.]+)[\s,]+([-\d.]+)[\s,]+([-\d.]+)\s*["']/i.exec(root);
    const vbW = viewBox ? Number(viewBox[3]) : NaN;
    const vbH = viewBox ? Number(viewBox[4]) : NaN;
    const hasNumeric = (name: string) => new RegExp(`\\s${name}\\s*=\\s*["']\\s*[\\d.]+(px)?\\s*["']`, "i").test(root);
    root = root.replace(/\s(fill|width|height)\s*=\s*("[^"]*"|'[^']*')/gi, (all, name: string) => {
        const lower = name.toLowerCase();
        if ((lower === "width" || lower === "height") && hasNumeric(lower)) return all;
        return "";
    });
    const additions: string[] = [];
    if (!hasNumeric("width")) additions.push(`width="${Number.isFinite(vbW) && vbW > 0 ? vbW : 256}"`);
    if (!hasNumeric("height")) additions.push(`height="${Number.isFinite(vbH) && vbH > 0 ? vbH : 256}"`);
    additions.push(`fill="${BRAND_NAVY}"`);
    root = root.replace(/\s*(\/?)>$/, ` ${additions.join(" ")}$1>`);
    const withoutStyle = svgText.replace(/<style\b[\s\S]*?<\/style>/gi, "");
    return withoutStyle.replace(/<svg\b[^>]*>/i, root);
}

export interface SignatureLayout {
    canvasWidth: number;
    canvasHeight: number;
    pad: number;
    emblemX: number;
    emblemY: number;
    emblemWidth: number;
    emblemHeight: number;
    gap: number;
    fontPx: number;
    textX: number;
    textY: number;
}

/**
 * The navbar's proportions: emblem height = 1.55 x the font size, gap = 0.3 x the emblem
 * height, text vertically centred on the emblem. `measureText(fontPx)` returns the name's width.
 */
export function layoutSignature(
    canvasHeight: number,
    emblemAspect: number,
    measureText: (fontPx: number) => number,
): SignatureLayout {
    const pad = Math.round(canvasHeight * 0.04);
    const emblemHeight = canvasHeight - pad * 2;
    const aspect = Number.isFinite(emblemAspect) && emblemAspect > 0 ? emblemAspect : 1;
    const emblemWidth = Math.round(emblemHeight * aspect);
    const fontPx = emblemHeight / 1.55;
    const gap = Math.round(emblemHeight * 0.3);
    const textX = pad + emblemWidth + gap;
    const textWidth = measureText(fontPx);
    return {
        canvasWidth: Math.ceil(textX + textWidth + pad),
        canvasHeight,
        pad,
        emblemX: pad,
        emblemY: pad,
        emblemWidth,
        emblemHeight,
        gap,
        fontPx,
        textX,
        textY: canvasHeight / 2,
    };
}

function defaultFetch(): FetchFn | null {
    return typeof fetch === "function" ? (fetch as unknown as FetchFn) : null;
}

/** An image source safe to draw: SVGs are fetched and forced to the light colour; others pass through. */
async function resolveImageSource(src: string, fetchFn: FetchFn | null): Promise<string | null> {
    if (!isSvgSource(src)) return src;
    if (src.startsWith("data:")) {
        const comma = src.indexOf(",");
        if (comma < 0) return null;
        const body = src.slice(comma + 1);
        const text = /;base64/i.test(src.slice(0, comma)) ? atob(body) : decodeURIComponent(body);
        return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(forceLightSvg(text))}`;
    }
    if (!fetchFn) return null;
    const response = await fetchFn(src);
    if (!response.ok) return null;
    const text = await response.text();
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(forceLightSvg(text))}`;
}

async function loadImage(src: string): Promise<HTMLImageElement | null> {
    const image = new Image();
    image.crossOrigin = "anonymous";
    const loaded = new Promise<boolean>((resolve) => {
        const timer = setTimeout(() => resolve(false), LOAD_TIMEOUT_MS);
        image.onload = () => { clearTimeout(timer); resolve(true); };
        image.onerror = () => { clearTimeout(timer); resolve(false); };
    });
    image.src = src;
    return (await loaded) ? image : null;
}

/** The brand mark alone as a PNG data URL (drawn on a canvas). Null on ANY failure; never throws. */
export async function loadLogoPng(
    src: string,
    sizePx = 96,
    fetchFn: FetchFn | null = defaultFetch(),
): Promise<CallExportLogo | null> {
    try {
        if (typeof document === "undefined" || typeof Image === "undefined") return null;
        const resolved = await resolveImageSource(src, fetchFn);
        if (!resolved) return null;
        const image = await loadImage(resolved);
        if (!image) return null;
        const naturalW = image.naturalWidth || sizePx;
        const naturalH = image.naturalHeight || sizePx;
        const scale = sizePx / Math.max(naturalW, naturalH);
        const width = Math.max(1, Math.round(naturalW * scale));
        const height = Math.max(1, Math.round(naturalH * scale));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        if (!context) return null;
        context.drawImage(image, 0, 0, width, height);
        const dataUrl = canvas.toDataURL("image/png");
        if (!dataUrl.startsWith("data:image/png;base64,")) return null;
        return { dataUrl, width, height };
    } catch {
        return null;
    }
}

/**
 * The website's signature as ONE PNG: the emblem and the brand name as the navbar shows them
 * (Manrope, medium, navy), always in the light colour. Null on ANY failure; never throws.
 */
export async function loadBrandSignaturePng(
    brand: CallExportBrand,
    opts: { heightPx?: number; fetchFn?: FetchFn | null } = {},
): Promise<CallExportLogo | null> {
    try {
        if (typeof document === "undefined" || typeof Image === "undefined") return null;
        const name = (brand.name ?? "").trim();
        if (!name) return null;
        const resolved = await resolveImageSource(brand.logoSrc, opts.fetchFn === undefined ? defaultFetch() : opts.fetchFn);
        if (!resolved) return null;

        let family = "";
        try { family = getComputedStyle(document.body).getPropertyValue("--font-manrope").trim(); } catch { /* fall back */ }
        if (!family) family = FALLBACK_FAMILY;
        try { await document.fonts.load(`500 64px ${family}`, name); } catch { /* measure with whatever is available */ }

        const image = await loadImage(resolved);
        if (!image) return null;
        const aspect = image.naturalWidth > 0 && image.naturalHeight > 0 ? image.naturalWidth / image.naturalHeight : 1;

        const scaleFactor = 3;
        const canvasHeight = Math.max(8, Math.round((opts.heightPx ?? 96) * scaleFactor));
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d");
        if (!context) return null;
        const applyFont = (fontPx: number) => {
            context.font = `500 ${fontPx}px ${family}`;
            try { (context as unknown as { letterSpacing: string }).letterSpacing = "-0.02em"; } catch { /* not supported */ }
        };
        const layout = layoutSignature(canvasHeight, aspect, (fontPx) => {
            applyFont(fontPx);
            return context.measureText(name).width;
        });
        if (!(layout.canvasWidth > layout.textX + layout.pad)) return null; // zero-size text
        canvas.width = layout.canvasWidth;
        canvas.height = layout.canvasHeight;
        // Resizing the canvas resets its state.
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, layout.emblemX, layout.emblemY, layout.emblemWidth, layout.emblemHeight);
        applyFont(layout.fontPx);
        context.fillStyle = BRAND_NAVY;
        context.textAlign = "left";
        context.textBaseline = "middle";
        context.fillText(name, layout.textX, layout.textY);
        const dataUrl = canvas.toDataURL("image/png");
        if (!dataUrl.startsWith("data:image/png;base64,")) return null;
        return { dataUrl, width: canvas.width, height: canvas.height, includesName: true };
    } catch {
        return null;
    }
}
