"use client";

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNotificationsActions } from "@/lib/notifications-client";
import { supportApi } from "@/lib/support-api";
import { supportQueryKeys } from "@/lib/support-config";
import { newlyUnread, useSupportUnread, type UnreadSnapshot } from "@/lib/support-unread";

/*
 * Turns a rising unread count into one in-app notification per newly unread
 * ticket, "New reply on SUP-…". The unread route returns ticket ids only, so
 * the number comes from the first page of the customer's own tickets; a
 * ticket not on that page is announced without its number.
 */
export function SupportUnreadNotifier() {
    const { snapshot } = useSupportUnread();
    const { create } = useNotificationsActions();
    const queryClient = useQueryClient();
    const previousRef = useRef<UnreadSnapshot | null>(null);

    useEffect(() => {
        if (!snapshot) return;
        const fresh = newlyUnread(previousRef.current, snapshot);
        previousRef.current = snapshot;
        if (!fresh.length) return;
        let cancelled = false;
        supportApi.listTickets({ scope: "mine" }).then((page) => {
            if (cancelled) return;
            void queryClient.invalidateQueries({ queryKey: supportQueryKeys.ticketsAll });
            const numbers = new Map(page.items.map((item) => [item.id, item.number] as const));
            for (const ticketId of fresh) {
                const number = numbers.get(ticketId);
                create({
                    type: "info",
                    title: number ? `New reply on ${number}` : "New reply on a support ticket",
                    message: "The support team answered. Open the ticket to read it.",
                    priority: "normal",
                    data: { kind: "support_reply", ticket_id: ticketId, href: `/support/tickets/${ticketId}` },
                });
            }
        }).catch(() => {
            if (cancelled) return;
            for (const ticketId of fresh) {
                create({ type: "info", title: "New reply on a support ticket", message: "Open Support to read it.", data: { kind: "support_reply", ticket_id: ticketId } });
            }
        });
        return () => { cancelled = true; };
    }, [snapshot, create, queryClient]);

    return null;
}
