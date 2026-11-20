import type { CallExportLogo } from "./columns";

const LOAD_TIMEOUT_MS = 5000;

/** The brand logo as a PNG data URL (drawn on a canvas). Null on ANY failure; never throws. */
export async function loadLogoPng(src: string, sizePx = 96): Promise<CallExportLogo | null> {
    try {
        if (typeof document === "undefined" || typeof Image === "undefined") return null;
        const image = new Image();
        image.crossOrigin = "anonymous";
        const loaded = new Promise<boolean>((resolve) => {
            const timer = setTimeout(() => resolve(false), LOAD_TIMEOUT_MS);
            image.onload = () => { clearTimeout(timer); resolve(true); };
            image.onerror = () => { clearTimeout(timer); resolve(false); };
        });
        image.src = src;
        if (!(await loaded)) return null;
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
