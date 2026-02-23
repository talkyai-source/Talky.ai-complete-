"use client";

import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AppNotification, NotificationType } from "@/lib/notifications";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";

const TYPE_META: Record<
    NotificationType,
    {
        Icon: typeof CheckCircle2;
        accentClass: string;
        ringClass: string;
        role: "status" | "alert";
        live: "polite" | "assertive";
        label: string;
    }
> = {
    success: {
        Icon: CheckCircle2,
        accentClass: "text-emerald-500",
        ringClass: "ring-emerald-500/15",
        role: "status",
        live: "polite",
        label: "Success",
    },
    warning: {
        Icon: AlertTriangle,
        accentClass: "text-amber-500",
        ringClass: "ring-amber-500/15",
        role: "status",
        live: "polite",
        label: "Warning",
    },
    error: {
        Icon: XCircle,
        accentClass: "text-red-500",
        ringClass: "ring-red-500/15",
        role: "alert",
        live: "assertive",
        label: "Error",
    },
    info: {
        Icon: Info,
        accentClass: "text-blue-500",
        ringClass: "ring-blue-500/15",
        role: "status",
        live: "polite",
        label: "Info",
    },
};

function playTone(type: NotificationType) {
    if (typeof window === "undefined") return;
    const ua = (navigator as unknown as { userActivation?: { hasBeenActive: boolean; isActive: boolean } }).userActivation;
    if (ua && !ua.hasBeenActive) return;
    const AudioContextCtor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextCtor) return;
    const ctx = new AudioContextCtor();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = type === "error" ? 220 : type === "warning" ? 330 : type === "success" ? 440 : 392;
    gain.gain.value = 0.02;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    const t = ctx.currentTime;
    osc.stop(t + 0.08);
    window.setTimeout(() => {
        ctx.close().catch(() => {});
    }, 160);
}

function Toast({ n, onClose }: { n: AppNotification; onClose: () => void }) {
    const meta = TYPE_META[n.type];
    const Icon = meta.Icon;
    return (
        <motion.div
            layout
            role={meta.role}
            aria-label={`${meta.label} notification`}
            aria-live={meta.live}
            initial={{ opacity: 0, y: -10, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.99 }}
            transition={{ type: "spring", stiffness: 320, damping: 26 }}
            className={cn(
                "pointer-events-auto w-[min(420px,calc(100vw-24px))] rounded-2xl border border-border bg-background/90 p-4 shadow-xl ring-1 backdrop-blur-md",
                meta.ringClass
            )}
        >
            <div className="flex items-start gap-3">
                <div className={cn("mt-0.5 shrink-0", meta.accentClass)}>
                    <Icon className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                            <div className="text-sm font-semibold text-foreground truncate">{n.title}</div>
                            {n.message ? <div className="mt-0.5 text-sm text-muted-foreground">{n.message}</div> : null}
                        </div>
                        <button
                            type="button"
                            onClick={onClose}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-xl text-muted-foreground hover:bg-foreground/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
                            aria-label="Dismiss notification"
                        >
                            <X className="h-4 w-4" />
                        </button>
                    </div>
                </div>
            </div>
        </motion.div>
    );
}

export function NotificationToaster() {
    const { toasts, settings } = useNotificationsState();
    const { dismissToast } = useNotificationsActions();
    const [popupOpen, setPopupOpen] = useState(() => {
        return Boolean((globalThis as unknown as { __talkleeNotificationsPopupOpen?: boolean }).__talkleeNotificationsPopupOpen);
    });

    const visible = useMemo(() => toasts.slice(0, 4), [toasts]);

    useEffect(() => {
        if (typeof window === "undefined") return;
        const onPopup = (e: Event) => {
            const detail = (e as CustomEvent<{ open?: boolean }>).detail;
            setPopupOpen(Boolean(detail?.open));
        };
        window.addEventListener("talklee:notifications-popup", onPopup as EventListener);
        return () => window.removeEventListener("talklee:notifications-popup", onPopup as EventListener);
    }, []);

    useEffect(() => {
        if (!settings.soundsEnabled) return;
        if (visible.length === 0) return;
        playTone(visible[0].type);
    }, [settings.soundsEnabled, visible]);

    useEffect(() => {
        if (visible.length === 0) return;
        const timers = visible.map((t) =>
            window.setTimeout(() => {
                dismissToast(t.id);
            }, Math.max(1200, settings.toastDurationMs))
        );
        return () => timers.forEach((id) => window.clearTimeout(id));
    }, [dismissToast, settings.toastDurationMs, visible]);

    if (popupOpen) return null;

    return (
        <div className="pointer-events-none fixed right-3 top-3 z-[60] flex flex-col gap-2">
            <AnimatePresence initial={false}>
                {visible.map((n) => (
                    <Toast key={n.id} n={n} onClose={() => dismissToast(n.id)} />
                ))}
            </AnimatePresence>
        </div>
    );
}
