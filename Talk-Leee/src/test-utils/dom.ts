import { JSDOM } from "jsdom";

let currentDom: JSDOM | null = null;

export function ensureDom() {
    if (typeof window !== "undefined" && typeof document !== "undefined") {
        try {
            if (typeof window.location?.origin === "string" && window.location.origin !== "null") return;
        } catch {
            return;
        }
    }

    const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>", {
        url: "http://localhost/",
    });

    currentDom = dom;

    const g = globalThis as unknown as Record<string, unknown>;

    const setProp = (key: string, value: unknown) => {
        Object.defineProperty(g, key, { value, configurable: true, writable: true });
    };

    setProp("window", dom.window as unknown as Window & typeof globalThis);
    setProp("document", dom.window.document);
    setProp("navigator", dom.window.navigator);
    setProp("Element", dom.window.Element);
    setProp("HTMLElement", dom.window.HTMLElement);
    setProp("Node", dom.window.Node);
    setProp("Event", dom.window.Event);
    setProp("CustomEvent", dom.window.CustomEvent);
    setProp("MouseEvent", dom.window.MouseEvent);
    setProp("KeyboardEvent", dom.window.KeyboardEvent);
    setProp("FocusEvent", dom.window.FocusEvent);
    setProp("PointerEvent", (dom.window as unknown as { PointerEvent?: unknown }).PointerEvent ?? dom.window.MouseEvent);
    setProp("getComputedStyle", dom.window.getComputedStyle.bind(dom.window));

    if (!globalThis.requestAnimationFrame) {
        setProp("requestAnimationFrame", (cb: FrameRequestCallback) => window.setTimeout(() => cb(Date.now()), 0) as unknown as number);
    }
    if (!globalThis.cancelAnimationFrame) {
        setProp("cancelAnimationFrame", (id: number) => window.clearTimeout(id));
    }
}

export function teardownDom() {
    if (!currentDom) return;
    (currentDom.window as unknown as { close: () => void }).close();
    currentDom = null;
}
