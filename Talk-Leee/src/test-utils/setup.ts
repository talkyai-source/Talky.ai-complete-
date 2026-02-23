import { ensureDom } from "@/test-utils/dom";

ensureDom();

(globalThis as unknown as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

if (typeof (globalThis as unknown as { self?: unknown }).self === "undefined") {
    (globalThis as unknown as { self: unknown }).self = globalThis as unknown;
}

if (typeof (globalThis as unknown as { requestIdleCallback?: unknown }).requestIdleCallback === "undefined") {
    const requestIdleCallback = (cb: (deadline: { didTimeout: boolean; timeRemaining: () => number }) => void) => {
        const start = Date.now();
        return setTimeout(() => {
            cb({
                didTimeout: false,
                timeRemaining: () => Math.max(0, 50 - (Date.now() - start)),
            });
        }, 1) as unknown as number;
    };
    const cancelIdleCallback = (id: number) => {
        clearTimeout(id);
    };
    (globalThis as unknown as { requestIdleCallback: unknown }).requestIdleCallback = requestIdleCallback;
    (globalThis as unknown as { cancelIdleCallback: unknown }).cancelIdleCallback = cancelIdleCallback;
}

if (typeof (globalThis as unknown as { ResizeObserver?: unknown }).ResizeObserver === "undefined") {
    class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
    }
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserver;
}

if (typeof (globalThis as unknown as { IntersectionObserver?: unknown }).IntersectionObserver === "undefined") {
    class MockIntersectionObserver {
        private callback: IntersectionObserverCallback;
        constructor(callback: IntersectionObserverCallback) {
            this.callback = callback;
        }
        observe(element: Element) {
            // Trigger immediately as intersecting
            this.callback([{ 
                isIntersecting: true, 
                target: element, 
                intersectionRatio: 1, 
                boundingClientRect: element.getBoundingClientRect(), 
                intersectionRect: element.getBoundingClientRect(), 
                rootBounds: null, 
                time: Date.now() 
            } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
        }
        unobserve() {}
        disconnect() {}
    }
    (globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver = MockIntersectionObserver;
    if (typeof window !== "undefined") {
        (window as unknown as { IntersectionObserver: unknown }).IntersectionObserver = MockIntersectionObserver;
    }
}
