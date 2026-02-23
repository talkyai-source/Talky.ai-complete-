"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { cn } from "@/lib/utils";
import { useNotificationsState } from "@/lib/notifications-client";
import { NotificationCenter } from "@/components/notifications/notification-center";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";

export function NotificationBell({ className }: { className?: string }) {
    const { unreadCount } = useNotificationsState();
    const [open, setOpen] = useState(false);
    const btnRef = useRef<HTMLButtonElement | null>(null);
    const [pos, setPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });

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
            const w = 460;
            const h = 420;
            const m = 10;
            const preferredLeft = rect.right - w;
            const preferredTop = rect.bottom + 10;
            const maxLeft = Math.max(m, window.innerWidth - m - w);
            const maxTop = Math.max(m, window.innerHeight - m - h);
            const left = Math.min(Math.max(preferredLeft, m), maxLeft);
            const top = Math.min(Math.max(preferredTop, m), maxTop);
            setPos({ left, top });
        };
        update();
        window.addEventListener("resize", update, { passive: true });
        window.addEventListener("scroll", update, true);
        return () => {
            window.removeEventListener("resize", update);
            window.removeEventListener("scroll", update, true);
        };
    }, [open]);

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
                                      className="absolute w-[460px] h-[420px] overflow-hidden rounded-2xl border border-border bg-background/90 backdrop-blur-xl shadow-2xl"
                                      style={{ left: pos.left, top: pos.top }}
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
