"use client";

import { useMemo } from "react";
import { Bell, CheckCheck, Circle, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AppNotification, NotificationType } from "@/lib/notifications";
import { useNotificationsActions, useNotificationsState } from "@/lib/notifications-client";
import { Button } from "@/components/ui/button";

function formatTimestamp(ms: number) {
    return new Date(ms).toLocaleString();
}

const TYPE_COLOR: Record<NotificationType, string> = {
    success: "text-emerald-500",
    warning: "text-amber-500",
    error: "text-red-500",
    info: "text-blue-500",
};

function NotificationRow({
    n,
    onMarkRead,
}: {
    n: AppNotification;
    onMarkRead: () => void;
}) {
    const unread = !n.readAt;
    return (
        <button
            type="button"
            onClick={onMarkRead}
            className={cn(
                "h-[72px] w-full overflow-hidden rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/20",
                unread ? "border-foreground/10 bg-foreground/5 hover:bg-foreground/7" : "border-border bg-background hover:bg-foreground/3"
            )}
            aria-label={unread ? "Mark notification as read" : "Notification"}
        >
            <div className="flex items-start gap-3">
                <div className={cn("mt-1 shrink-0", TYPE_COLOR[n.type])}>
                    {unread ? <Circle className="h-3.5 w-3.5" /> : <span className="h-3.5 w-3.5" />}
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                            <div className={cn("truncate text-sm", unread ? "font-semibold text-foreground" : "font-medium text-foreground")}>
                                {n.title}
                            </div>
                            {n.message ? <div className="mt-0.5 truncate text-sm text-muted-foreground">{n.message}</div> : null}
                        </div>
                        <div className="shrink-0 text-xs text-muted-foreground">{formatTimestamp(n.createdAt)}</div>
                    </div>
                </div>
            </div>
        </button>
    );
}

export function NotificationCenter({
    className,
    maxHeightClassName = "h-[232px]",
    showUnreadBadge = true,
    actionsPlacement = "header",
    listFill = false,
}: {
    className?: string;
    maxHeightClassName?: string;
    showUnreadBadge?: boolean;
    actionsPlacement?: "header" | "footer";
    listFill?: boolean;
}) {
    const { notifications, unreadCount } = useNotificationsState();
    const { markRead, markAllRead, clearAll } = useNotificationsActions();

    const ordered = useMemo(() => {
        return [...notifications].sort((a, b) => b.createdAt - a.createdAt);
    }, [notifications]);

    return (
        <div className={cn("flex h-full flex-col", className)}>
            <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
                <div className="min-w-0">
                    <div className="flex items-center gap-2">
                        <Bell className="h-5 w-5 text-muted-foreground" />
                        <div className="text-base font-semibold text-foreground">Notifications</div>
                        {showUnreadBadge && unreadCount ? (
                            <span className="rounded-full bg-foreground/10 px-2 py-0.5 text-xs font-semibold text-foreground">
                                {unreadCount} unread
                            </span>
                        ) : null}
                    </div>
                </div>
                {actionsPlacement === "header" ? (
                    <div className="flex items-center gap-2">
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={markAllRead}
                            className="h-9"
                            aria-label="Mark all notifications as read"
                        >
                            <CheckCheck className="h-4 w-4" />
                            Mark all read
                        </Button>
                        <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={clearAll}
                            className="h-9"
                            aria-label="Clear notification history"
                        >
                            <Trash2 className="h-4 w-4" />
                            Clear
                        </Button>
                    </div>
                ) : null}
            </div>

            <div className={cn("min-h-0 flex-1 px-4 py-4", listFill ? "flex flex-col" : undefined)}>
                {ordered.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-border px-5 py-8 text-center">
                        <div className="text-sm font-semibold text-foreground">No notifications</div>
                        <div className="mt-1 text-sm text-muted-foreground">You’re all caught up.</div>
                    </div>
                ) : (
                    <div
                        className={cn(
                            "min-h-0 overflow-y-auto overscroll-contain",
                            listFill ? "flex-1" : maxHeightClassName
                        )}
                        aria-label="Notification list"
                    >
                        <div className="space-y-2">
                            {ordered.map((n) => (
                                <NotificationRow key={n.id} n={n} onMarkRead={() => markRead(n.id)} />
                            ))}
                        </div>
                    </div>
                )}
            </div>

            {actionsPlacement === "footer" ? (
                <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={markAllRead}
                        className="h-9"
                        aria-label="Mark all notifications as read"
                    >
                        <CheckCheck className="h-4 w-4" />
                        Mark all read
                    </Button>
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={clearAll}
                        className="h-9"
                        aria-label="Clear notification history"
                    >
                        <Trash2 className="h-4 w-4" />
                        Clear
                    </Button>
                </div>
            ) : null}
        </div>
    );
}
