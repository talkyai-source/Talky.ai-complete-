"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    describeSupportError, isSupportUnavailable, newRequestId, supportApi, supportErrorCode, supportErrorStatus,
    type PageContext, type SupportConfig,
} from "@/lib/support-api";
import { supportQueryKeys } from "@/lib/support-config";
import { supportChatStore, EMPTY_SUPPORT_DRAWER_STATE, type SupportChatStore } from "@/lib/support-chat-store";
import { readSupportConversation, writeSupportConversation, type SupportIdentity } from "@/lib/support-session";
import { uploadedAttachmentIds } from "@/lib/support-attachments";
import { ticketDraftPayload } from "@/lib/support-ticket-draft";
import type { PendingWrite } from "@/lib/support-chat-state";

/*
 * Executes whatever write the chat machine names in `pending`, exactly once
 * per request_id, and feeds the result back as an event. A failed write keeps
 * its id in the machine; Retry re-executes it unchanged. When a conversation
 * write fails for a reason that leaves the outcome unknown (network, timeout,
 * 5xx), the conversation is re-read first: if the server already holds the
 * reply, that is shown instead of a retry.
 */

const subscribe = (listener: () => void) => supportChatStore.subscribe(listener);
const getSnapshot = () => supportChatStore.getSnapshot();
const getServerSnapshot = () => EMPTY_SUPPORT_DRAWER_STATE;

export function useSupportDrawerState() {
    return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

function outcomeUnknown(error: unknown): boolean {
    const status = supportErrorStatus(error);
    const code = supportErrorCode(error);
    if (status !== undefined) return status >= 500;
    return code === "network_error" || code === "timeout" || code === "aborted" || code === null;
}

export function useSupportChat(input: { config: SupportConfig | null; identity: SupportIdentity | null; pageContext: () => PageContext }) {
    const state = useSupportDrawerState();
    const queryClient = useQueryClient();
    const { config, identity } = input;
    const { pageContext } = input;
    const { chat, open, draft, attachments } = state;

    // Opening an idle drawer resumes the stored conversation or starts a new one.
    useEffect(() => {
        if (!open || !identity || !config || chat.phase !== "idle" || chat.pending) return;
        let cancelled = false;
        const stored = readSupportConversation(identity);
        const startFresh = () => {
            writeSupportConversation(identity, null);
            supportChatStore.dispatch({ type: "start", requestId: newRequestId() });
        };
        if (!stored) { startFresh(); return; }
        supportApi.getConversation(stored).then((conversation) => {
            if (cancelled) return;
            supportChatStore.dispatch({ type: "resumed", conversation, suggestions: config.suggestions });
            if (supportChatStore.getSnapshot().chat.phase === "idle") startFresh();
        }).catch((error: unknown) => {
            if (cancelled) return;
            if (isSupportUnavailable(error) || supportErrorStatus(error) === 403) startFresh();
            else supportChatStore.dispatch({ type: "fail", message: describeSupportError(error) });
        });
        return () => { cancelled = true; };
    }, [open, identity, config, chat.phase, chat.pending]);

    // Execute the pending write once per request_id.
    useEffect(() => {
        const pending = chat.pending;
        if (!pending || chat.phase === "failed" || !identity) return;
        if (!supportChatStore.beginWrite(pending.requestId)) return;
        const conversationId = chat.conversationId;
        const knownMessages = chat.messages.length;
        const finish = () => supportChatStore.endWrite(pending.requestId);
        const fail = (error: unknown) => supportChatStore.dispatch({ type: "fail", message: describeSupportError(error) });
        const reconcile = async (error: unknown) => {
            if (!conversationId || !outcomeUnknown(error)) { fail(error); return; }
            try {
                const conversation = await supportApi.getConversation(conversationId);
                if (conversation.messages.length > knownMessages || conversation.escalation || conversation.status !== "active") {
                    supportChatStore.dispatch({ type: "resumed", conversation, suggestions: config?.suggestions ?? [] });
                    return;
                }
            } catch { /* The re-read failed too; fall through to the retryable failure. */ }
            fail(error);
        };
        void run(pending).finally(finish);

        async function run(write: PendingWrite) {
            try {
                switch (write.kind) {
                    case "start": {
                        const result = await supportApi.startConversation(write.requestId, pageContext());
                        writeSupportConversation(identity!, result.data.conversation_id);
                        supportChatStore.dispatch({ type: "started", conversationId: result.data.conversation_id, greeting: result.data.greeting, suggestions: result.data.suggestions, at: new Date().toISOString() });
                        return;
                    }
                    case "message": {
                        if (!conversationId) throw new Error("No conversation is open.");
                        const result = await supportApi.sendMessage(conversationId, write.requestId, write.content);
                        supportChatStore.dispatch({ type: "bot_reply", message: result.data.message, resolutionPrompt: result.data.resolution_prompt, escalation: result.data.escalation });
                        return;
                    }
                    case "feedback": {
                        if (!conversationId) throw new Error("No conversation is open.");
                        const result = await supportApi.sendFeedback(conversationId, write.requestId, write.solved);
                        if (write.solved) writeSupportConversation(identity!, null);
                        supportChatStore.dispatch({ type: "feedback_result", solved: write.solved, message: result.data.message, escalation: result.data.escalation });
                        return;
                    }
                    case "escalate": {
                        if (!conversationId) throw new Error("No conversation is open.");
                        const result = await supportApi.escalate(conversationId, write.requestId);
                        supportChatStore.dispatch({ type: "escalated", escalation: result.data.escalation });
                        return;
                    }
                    case "create_ticket": {
                        const current = supportChatStore.getSnapshot();
                        if (!current.draft) throw new Error("The ticket form is empty.");
                        const payload = ticketDraftPayload(current.draft, conversationId, uploadedAttachmentIds(current.attachments));
                        const result = await supportApi.createTicket(write.requestId, payload);
                        writeSupportConversation(identity!, null);
                        await queryClient.invalidateQueries({ queryKey: supportQueryKeys.ticketsAll });
                        supportChatStore.dispatch({ type: "ticket_created", ticketId: result.data.id, number: result.data.number });
                        return;
                    }
                    case "append_to_ticket": {
                        const current = supportChatStore.getSnapshot();
                        const existing = current.chat.escalation?.existing_ticket;
                        if (!current.draft || !existing) throw new Error("The ticket form is empty.");
                        const body = `${current.draft.subject.trim()}\n\n${current.draft.description.trim()}`.trim();
                        await supportApi.replyToTicket(write.ticketId, write.requestId, body, uploadedAttachmentIds(current.attachments));
                        writeSupportConversation(identity!, null);
                        await queryClient.invalidateQueries({ queryKey: supportQueryKeys.ticketsAll });
                        supportChatStore.dispatch({ type: "appended", ticketId: existing.id, number: existing.number });
                        return;
                    }
                }
            } catch (error) {
                if (write.kind === "message" || write.kind === "feedback" || write.kind === "escalate") await reconcile(error);
                else fail(error);
            }
        }
        // The write runs to completion even if this layout unmounts mid-flight; the store outlives it.
    }, [chat.pending, chat.phase, chat.conversationId, chat.messages.length, identity, config, pageContext, queryClient]);

    const openDrawer = useCallback(() => supportChatStore.setOpen(true), []);
    const closeDrawer = useCallback(() => supportChatStore.setOpen(false), []);
    const send = useCallback((content: string) => {
        const text = content.trim();
        if (!text) return;
        supportChatStore.dispatch({ type: "send", requestId: newRequestId(), content: text.slice(0, 2000), at: new Date().toISOString() });
    }, []);
    const feedback = useCallback((solved: boolean) => supportChatStore.dispatch({ type: "feedback", requestId: newRequestId(), solved }), []);
    const escalate = useCallback(() => supportChatStore.dispatch({ type: "escalate", requestId: newRequestId() }), []);
    const chooseExisting = useCallback((choice: "append" | "new") => supportChatStore.dispatch({ type: "choose_existing", choice }), []);
    const submitTicket = useCallback(() => supportChatStore.dispatch({ type: "submit", requestId: newRequestId() }), []);
    const appendToExisting = useCallback(() => {
        const existing = supportChatStore.getSnapshot().chat.escalation?.existing_ticket;
        if (existing) supportChatStore.dispatch({ type: "append", requestId: newRequestId(), ticket: existing });
    }, []);
    const retry = useCallback(() => supportChatStore.dispatch({ type: "retry" }), []);
    const draftWithoutBot = useCallback(() => supportChatStore.dispatch({ type: "draft_without_bot" }), []);
    const startOver = useCallback(() => {
        if (identity) writeSupportConversation(identity, null);
        supportChatStore.reset();
    }, [identity]);

    return {
        state: chat, open, draft, attachments,
        openDrawer, closeDrawer, send, feedback, escalate, chooseExisting, submitTicket, appendToExisting, retry, draftWithoutBot, startOver,
        setDraft, setAttachments,
    };
}

const setDraft: SupportChatStore["setDraft"] = (draft) => supportChatStore.setDraft(draft);
const setAttachments: SupportChatStore["setAttachments"] = (update) => supportChatStore.setAttachments(update);
