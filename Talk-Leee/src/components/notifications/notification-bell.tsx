"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { cn } from "@/lib/utils";
import { useNotificationsState } from "@/lib/notifications-client";
import { NotificationCenter } from "@/components/notifications/notification-center";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";

// Desktop (>=1024px) popup size — unchanged from the original fixed layout.
const DESKTOP_WIDTH = 460;
const DESKTOP_HEIGHT = 420;
const DESKTOP_BREAKPOINT = 1024;

// Mobile/tablet sizing is derived from item count, matching the fixed
// CSS metrics in NotificationCenter (h-[72px] rows, space-y-2 gaps,
// px-4/py-3 chrome) rather than measuring the DOM at runtime.
const MOBILE_MAX_WIDTH = 340;
const MOBILE_WIDTH_RATIO = 0.85;
const ROW_HEIGHT = 72;
const ROW_GAP = 8;
const LIST_PADDING_Y = 32; // px-4 py-4 wrapper around the list/empty-state
const HEADER_HEIGHT = 49; // icon+title row + py-3 padding + border
const FOOTER_HEIGHT = 61; // action buttons row (h-9) + py-3 padding + border
const EMPTY_STATE_HEIGHT = 128; // dashed empty-state box when count === 0
// The popup's own outer box (this component's motion.div) has a 1px
// border on every side. With border-box sizing, setting that element's
// `style.height` to exactly the content total leaves its content box
// (and therefore NotificationCenter's h-full) 2px short — clipping the
// last row and forcing a scrollbar even when the count fits exactly.
const PANEL_BORDER_Y = 2;

function computeMobileWidth(viewportWidth: number) {
    return Math.min(MOBILE_MAX_WIDTH, Math.round(viewportWidth * MOBILE_WIDTH_RATIO));
}

// The panel's natural (uncapped) height for the given notification count:
// header + footer + list padding + up to 5 rows, plus the outer border.
// Any "must not run past the bottom of the screen" clamping happens
// where this is used, based on where the panel actually ends up
// positioned.
function computeMobileNaturalHeight(count: number) {
    const visibleRows = Math.min(count, 5);
    const rowsHeight =
        count === 0 ? EMPTY_STATE_HEIGHT : visibleRows * ROW_HEIGHT + Math.max(visibleRows - 1, 0) * ROW_GAP;
    return HEADER_HEIGHT + rowsHeight + LIST_PADDING_Y + FOOTER_HEIGHT + PANEL_BORDER_Y;
}

export function NotificationBell({ className }: { className?: string }) {
    const { unreadCount, notifications } = useNotificationsState();
    const [open, setOpen] = useState(false);
    const btnRef = useRef<HTMLButtonElement | null>(null);
    const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });
    const [size, setSize] = useState<{ w: number; h: number }>({ w: DESKTOP_WIDTH, h: DESKTOP_HEIGHT });

    const badge = useMemo(() => {
        if (!unreadCount) return null;
        const text = unreadCount > 99 ? "99+" : String(unreadCount);
        return (
            <span className="absolute -right-1 -top-1 min-w-5 rounded-full bg-red-500 px-1.5 py-0.5 text-[11px] font-bold leading-none text-white">
                {text}
            </span>
        );
    }, [unreadCount]);

    useEffect(() => {
        if (!open) return;
        const update = () => {
            const el = btnRef.current;
            if (!el) return;
            const rect = el.getBoundingClientRect();
            const isDesktop = window.innerWidth >= DESKTOP_BREAKPOINT;
            const w = isDesktop ? DESKTOP_WIDTH : computeMobileWidth(window.innerWidth);
            const m = 10;
            const preferredLeft = rect.right - w;
            const maxLeft = Math.max(m, window.innerWidth - m - w);
            const left = Math.min(Math.max(preferredLeft, m), maxLeft);

            let top: number;
            let h: number;
            if (isDesktop) {
                // Unchanged from the original fixed layout: fixed
                // 420px height, top clamped to stay on-screen.
                const preferredTop = rect.bottom + 10;
                const maxTop = Math.max(m, window.innerHeight - m - DESKTOP_HEIGHT);
                top = Math.min(Math.max(preferredTop, m), maxTop);
                h = DESKTOP_HEIGHT;
            } else {
                // The popup must always open BENEATH the bell — never
                // above or over it. On a short screen (e.g. a landscape
                // phone) it shrinks instead, so the list scrolls inside
                // a smaller box rather than the whole panel sliding up
                // over the button that opened it.
                top = Math.max(rect.bottom + 10, m);
                const naturalH = computeMobileNaturalHeight(notifications.length);
                const minPanelHeight = HEADER_HEIGHT + FOOTER_HEIGHT + PANEL_BORDER_Y;
                const availableBelow = window.innerHeight - top - m;
                h = Math.max(minPanelHeight, Math.min(naturalH, availableBelow));
            }
            setPos({ left, top });
            setSize({ w, h });
        };
        update();
        window.addEventListener("resize", update, { passive: true });
        window.addEventListener("scroll", update, true);
        window.addEventListener("orientationchange", update);
        return () => {
            window.removeEventListener("resize", update);
            window.removeEventListener("scroll", update, true);
            window.removeEventListener("orientationchange", update);
        };
    }, [open, notifications.length]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        window.dispatchEvent(new CustomEvent("talklee:notifications-popup", { detail: { open } }));
        (globalThis as unknown as { __talkleeNotificationsPopupOpen?: boolean }).__talkleeNotificationsPopupOpen = open;
        return () => {
            window.dispatchEvent(new CustomEvent("talklee:notifications-popup", { detail: { open: false } }));
            (globalThis as unknown as { __talkleeNotificationsPopupOpen?: boolean }).__talkleeNotificationsPopupOpen = false;
        };
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key === "Escape") setOpen(false);
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [open]);

    return (
        <>
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                ref={btnRef}
                className={cn(
                    "relative inline-flex items-center justify-center w-10 h-10 rounded-xl text-muted-foreground hover:text-foreground hover:bg-foreground/5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20",
                    className
                )}
                aria-label="Open notification center"
                aria-haspopup="dialog"
                aria-expanded={open}
            >
                <Bell className="w-5 h-5" />
                {badge}
            </button>
            {typeof document !== "undefined"
                ? createPortal(
                      <AnimatePresence>
                          {open ? (
                              <div className="fixed inset-0 z-50">
                                  <button
                                      type="button"
                                      className="absolute inset-0 bg-transparent"
                                      aria-label="Close notifications"
                                      onClick={() => setOpen(false)}
                                  />
                                  <motion.div
                                      role="dialog"
                                      aria-modal="true"
                                      aria-label="Notifications"
                                      className="absolute overflow-hidden rounded-2xl border border-border bg-background/90 backdrop-blur-xl shadow-2xl"
                                      style={{ left: pos.left, top: pos.top, width: size.w, height: size.h }}
                                      initial={{ opacity: 0, y: -6, scale: 0.99 }}
                                      animate={{ opacity: 1, y: 0, scale: 1 }}
                                      exit={{ opacity: 0, y: -6, scale: 0.99 }}
                                      transition={{ type: "spring", stiffness: 260, damping: 24 }}
                                      data-notifications-popup="true"
                                  >
                                      <NotificationCenter
                                          className="h-full"
                                          showUnreadBadge={false}
                                          actionsPlacement="footer"
                                          listFill
                                      />
                                  </motion.div>
                              </div>
                          ) : null}
                      </AnimatePresence>,
                      document.body
                  )
                : null}
        </>
    );
}
