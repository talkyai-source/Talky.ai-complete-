/**
 * The sideways navigation strip on small screens (index.css, max-width 600px).
 *
 * Returns the scroll position that shows one entry in full: unchanged when the
 * entry is already inside the visible part, otherwise the nearest position
 * that brings it in. `itemStart` and `itemEnd` are measured from the start of
 * the strip's scrolled content.
 */
export function stripScrollLeftToReveal(scrollLeft: number, viewWidth: number, itemStart: number, itemEnd: number): number {
    if (itemStart < scrollLeft) return Math.max(0, itemStart);
    if (itemEnd > scrollLeft + viewWidth) return Math.max(0, Math.min(itemStart, itemEnd - viewWidth));
    return scrollLeft;
}

/**
 * Scrolls the strip so one entry is fully visible. Does nothing when the
 * navigation does not scroll sideways (the column layout from 601px up).
 */
export function revealNavEntry(nav: HTMLElement | null, entry: Element | null): void {
    if (!nav || !entry || nav.scrollWidth <= nav.clientWidth) return;
    const navRect = nav.getBoundingClientRect();
    const itemRect = entry.getBoundingClientRect();
    const itemStart = itemRect.left - navRect.left + nav.scrollLeft;
    const next = stripScrollLeftToReveal(nav.scrollLeft, nav.clientWidth, itemStart, itemStart + itemRect.width);
    if (next !== nav.scrollLeft) nav.scrollLeft = next;
}

/** Scrolls the strip so its current entry is fully visible. */
export function revealCurrentNavEntry(nav: HTMLElement | null): void {
    revealNavEntry(nav, nav ? nav.querySelector('[aria-current="page"]') : null);
}
