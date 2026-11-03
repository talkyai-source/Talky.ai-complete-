import type { CSSProperties } from 'react';

export interface FloatingAnchor { top: number; bottom: number; left: number; right: number }
const FLOATING_MENU_GAP = 4, FLOATING_MENU_MARGIN = 8;
// Rendered off-screen until measured so an unplaced frame never shows clipped.
export const FLOATING_MENU_HIDDEN: CSSProperties = { position: 'fixed', top: -10000, left: -10000, right: 'auto', zIndex: 150 };

/** Viewport placement for an open row menu: below its button, right-aligned to
 *  it, flipped above when there is no room below and clamped inside the window. */
export function placeFloatingMenu(anchor: FloatingAnchor, menu: { width: number; height: number },
    viewport: { width: number; height: number }): CSSProperties {
    const below = anchor.bottom + FLOATING_MENU_GAP;
    const fitsBelow = below + menu.height <= viewport.height - FLOATING_MENU_MARGIN;
    const top = Math.max(FLOATING_MENU_MARGIN, fitsBelow ? below : anchor.top - FLOATING_MENU_GAP - menu.height);
    const left = Math.max(FLOATING_MENU_MARGIN, Math.min(anchor.right - menu.width, viewport.width - FLOATING_MENU_MARGIN - menu.width));
    return { position: 'fixed', top, left, right: 'auto', zIndex: 150 };
}
