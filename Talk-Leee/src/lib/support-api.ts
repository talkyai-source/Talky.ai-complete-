import { z } from "zod";
import { api, sharedHttpClient } from "@/lib/api";
import { isApiClientError } from "@/lib/http-client";

/*
 * Customer support API: the 17 /support routes, modelled on what the backend
 * returns today (backend/app/domain/services/support/projection.py and the
 * services), not on the brief's contract where the two differ.
 *
 * Reads go through api.request. Writes go through the shared client's
 * requestRaw so the Idempotent-Replay header and the real status (201 on a
 * first write, 200 on a replay) are visible to the caller; both paths share
 * the one HttpClient, its token, refresh and session-expired handling.
 */

export const TICKET_STATUSES = ["open", "in_progress", "waiting_on_customer", "resolved", "closed"] as const;
export const TICKET_PRIORITIES = ["urgent", "high", "normal", "low"] as const;
export const SUPPORT_CATEGORY_IDS = [
    "billing", "calls_campaigns", "phone_numbers", "ai_agent_voices", "integrations",
    "account_login", "bug", "feature_request", "privacy_data", "other",
] as const;
export const CONVERSATION_STATUSES = ["active", "resolved_by_bot", "escalated", "abandoned"] as const;
export const CUSTOMER_ACTIONS = ["reply", "resolve", "reopen", "rate"] as const;

const id = z.string().min(1);
const stamp = z.string().min(1);
const ticketNumber = z.string().regex(/^SUP-\d{6,}$/);

export const ticketStatusSchema = z.enum(TICKET_STATUSES);
export const ticketPrioritySchema = z.enum(TICKET_PRIORITIES);
export const supportCategorySchema = z.enum(SUPPORT_CATEGORY_IDS);

export const supportConfigSchema = z.object({
    enabled: z.boolean(),
    bot_enabled: z.boolean(),
    permissions: z.object({ report: z.boolean(), read_team: z.boolean() }),
    categories: z.array(z.object({ id: z.string().min(1), label: z.string().min(1) })),
    suggestions: z.array(z.string()),
    attachments: z.object({
        max_mb: z.number().int().nonnegative(),
        max_per_message: z.number().int().nonnegative(),
        types: z.array(z.string()),
    }),
});

export const articleSearchSchema = z.object({
    items: z.array(z.object({
        slug: z.string().min(1), title: z.string(), section: z.string(), snippet: z.string(), url: z.string(),
    })),
});

export const botSourceSchema = z.object({ slug: z.string(), title: z.string(), url: z.string() });
export const botMessageSchema = z.object({
    id, role: z.enum(["user", "assistant"]), content: z.string(), sources: z.array(botSourceSchema), created_at: stamp,
});

export const existingTicketSchema = z.object({ id, number: ticketNumber, subject: z.string(), status: ticketStatusSchema });
export const escalationSchema = z.object({
    reason: z.string().min(1),
    priority_hint: ticketPrioritySchema,
    draft: z.object({
        subject: z.string(), category: z.string().min(1), description: z.string(),
        related_campaign_id: z.string().optional(), related_call_id: z.string().optional(),
    }),
    existing_ticket: existingTicketSchema.nullable(),
});

export const conversationStartSchema = z.object({
    conversation_id: id, greeting: z.string(), suggestions: z.array(z.string()),
});
export const conversationSchema = z.object({
    conversation_id: id, status: z.enum(CONVERSATION_STATUSES),
    messages: z.array(botMessageSchema), escalation: escalationSchema.nullable(),
});
export const botReplySchema = z.object({
    message: botMessageSchema, resolution_prompt: z.boolean(), escalation: escalationSchema.nullable(),
});
export const feedbackResultSchema = z.object({
    status: z.enum(CONVERSATION_STATUSES), message: botMessageSchema.nullable(), escalation: escalationSchema.nullable(),
});
export const escalateResultSchema = z.object({ escalation: escalationSchema });

export const attachmentUploadSchema = z.object({
    attachment_id: id, filename: z.string(), content_type: z.string(), size_bytes: z.number().int().nonnegative(),
});
export const attachmentRefSchema = z.object({
    id, filename: z.string(), size_bytes: z.number().int().nonnegative(), content_type: z.string(),
});
// author_type stays a plain string: the thread ignores authors it does not know.
export const ticketMessageSchema = z.object({
    id, author_type: z.string().min(1), author_name: z.string().nullable(), body: z.string(),
    attachments: z.array(attachmentRefSchema), created_at: stamp,
});
export const ticketSchema = z.object({
    id, number: ticketNumber, subject: z.string(), category: z.string().min(1),
    priority: ticketPrioritySchema, status: ticketStatusSchema, created_at: stamp, updated_at: stamp,
    requester: z.object({ id: z.string().nullable(), name: z.string().nullable() }),
    related: z.object({ campaign_id: z.string().optional(), call_id: z.string().optional() }),
    messages: z.array(ticketMessageSchema),
    csat: z.object({ score: z.number().int().min(1).max(5), comment: z.string().nullable() }).nullable(),
    allowed_actions: z.array(z.enum(CUSTOMER_ACTIONS)),
});
export const ticketListItemSchema = z.object({
    id, number: ticketNumber, subject: z.string(), status: ticketStatusSchema, priority: ticketPrioritySchema,
    category: z.string().min(1), updated_at: stamp, unread: z.boolean(),
});
export const ticketPageSchema = z.object({ items: z.array(ticketListItemSchema), next_cursor: z.string().nullable() });
export const unreadSchema = z.object({ count: z.number().int().nonnegative(), ticket_ids: z.array(id) });

export type SupportConfig = z.infer<typeof supportConfigSchema>;
export type ArticleHit = z.infer<typeof articleSearchSchema>["items"][number];
export type BotMessage = z.infer<typeof botMessageSchema>;
export type BotSource = z.infer<typeof botSourceSchema>;
export type Escalation = z.infer<typeof escalationSchema>;
export type ExistingTicket = z.infer<typeof existingTicketSchema>;
export type ConversationStart = z.infer<typeof conversationStartSchema>;
export type Conversation = z.infer<typeof conversationSchema>;
export type BotReply = z.infer<typeof botReplySchema>;
export type FeedbackResult = z.infer<typeof feedbackResultSchema>;
export type AttachmentUpload = z.infer<typeof attachmentUploadSchema>;
export type AttachmentRef = z.infer<typeof attachmentRefSchema>;
export type TicketMessage = z.infer<typeof ticketMessageSchema>;
export type Ticket = z.infer<typeof ticketSchema>;
export type TicketListItem = z.infer<typeof ticketListItemSchema>;
export type TicketPage = z.infer<typeof ticketPageSchema>;
export type TicketStatus = (typeof TICKET_STATUSES)[number];
export type TicketPriority = (typeof TICKET_PRIORITIES)[number];

export type PageContext = { route?: string; campaign_id?: string; call_id?: string; app_version?: string; viewport?: string };
export type TicketDraftInput = {
    conversation_id?: string; subject: string; category: string; description: string;
    related_campaign_id?: string; related_call_id?: string; attachment_ids: string[];
};
/** A write's parsed body plus whether the server replayed an earlier identical request. */
export type WriteResult<T> = { data: T; replayed: boolean; status: number };

export function newRequestId(): string {
    return crypto.randomUUID();
}

export function supportErrorCode(error: unknown): string | null {
    const code = (error as { code?: unknown } | null)?.code;
    return typeof code === "string" && code.trim() ? code.trim() : null;
}
export function supportErrorStatus(error: unknown): number | undefined {
    const status = (error as { status?: unknown } | null)?.status;
    return typeof status === "number" ? status : undefined;
}
/** True when the server says support is switched off for this account (404 not_found on any support route). */
export function isSupportUnavailable(error: unknown): boolean {
    return supportErrorStatus(error) === 404;
}
/** The one message the UI shows for a rejected support write, by the server's own code. */
export function describeSupportError(error: unknown): string {
    const code = supportErrorCode(error);
    const status = supportErrorStatus(error);
    if (status === 429 || code === "rate_limited") {
        const retryAfterMs = (error as { retryAfterMs?: unknown } | null)?.retryAfterMs;
        const seconds = typeof retryAfterMs === "number" ? Math.max(1, Math.ceil(retryAfterMs / 1000)) : null;
        return seconds ? `You're sending messages too quickly. Wait about ${seconds}s and retry.` : "You're sending messages too quickly. Wait a moment and retry.";
    }
    if (code === "request_in_progress") return "That message is still being processed. Retry the same message in a moment.";
    if (code === "request_id_conflict") return "This request was already used for something else. Reload the conversation before continuing.";
    if (code === "conversation_closed") return "This conversation has ended. Start a new one.";
    if (code === "conversation_already_escalated") return "A ticket was already opened from this conversation.";
    if (code === "invalid_status_transition") return "That action is not allowed for this ticket's current status.";
    if (status === 404) return "Support isn't available yet.";
    if (isApiClientError(error)) return error.message;
    return error instanceof Error ? error.message : "The request could not be completed.";
}

function parse<T>(schema: z.ZodType<T>, data: unknown, what: string): T {
    const result = schema.safeParse(data);
    if (result.success) return result.data;
    throw new Error(`Unexpected ${what} response from the server.`);
}

async function write<T>(schema: z.ZodType<T>, what: string, opts: { path: string; body: unknown; signal?: AbortSignal }): Promise<WriteResult<T>> {
    const response = await sharedHttpClient().requestRaw({ path: opts.path, method: "POST", body: opts.body, signal: opts.signal, timeoutMs: 45_000 });
    const replayed = (response.headers.get("idempotent-replay") ?? "").toLowerCase() === "true";
    const data = parse(schema, await response.json(), what);
    return { data, replayed, status: response.status };
}

export const supportApi = {
    async config(signal?: AbortSignal): Promise<SupportConfig> {
        return parse(supportConfigSchema, await api.request({ path: "/support/config", method: "GET", signal, timeoutMs: 12_000, suppressAuthRedirect: true }), "support config");
    },
    async searchArticles(q: string, signal?: AbortSignal): Promise<ArticleHit[]> {
        return parse(articleSearchSchema, await api.request({ path: "/support/articles/search", method: "GET", query: { q }, signal }), "article search").items;
    },
    startConversation(requestId: string, pageContext: PageContext, signal?: AbortSignal) {
        return write(conversationStartSchema, "conversation", { path: "/support/conversations", body: { request_id: requestId, page_context: pageContext }, signal });
    },
    async getConversation(conversationId: string, signal?: AbortSignal): Promise<Conversation> {
        return parse(conversationSchema, await api.request({ path: `/support/conversations/${encodeURIComponent(conversationId)}`, method: "GET", signal }), "conversation");
    },
    sendMessage(conversationId: string, requestId: string, content: string, signal?: AbortSignal) {
        return write(botReplySchema, "assistant reply", { path: `/support/conversations/${encodeURIComponent(conversationId)}/messages`, body: { request_id: requestId, content }, signal });
    },
    sendFeedback(conversationId: string, requestId: string, solved: boolean, signal?: AbortSignal) {
        return write(feedbackResultSchema, "feedback", { path: `/support/conversations/${encodeURIComponent(conversationId)}/feedback`, body: { request_id: requestId, solved }, signal });
    },
    escalate(conversationId: string, requestId: string, signal?: AbortSignal) {
        return write(escalateResultSchema, "escalation", { path: `/support/conversations/${encodeURIComponent(conversationId)}/escalate`, body: { request_id: requestId }, signal });
    },
    async uploadAttachment(file: File, signal?: AbortSignal): Promise<AttachmentUpload> {
        const form = new FormData();
        form.append("file", file, file.name);
        const response = await sharedHttpClient().requestRaw({ path: "/support/attachments", method: "POST", body: form, signal, timeoutMs: 120_000 });
        return parse(attachmentUploadSchema, await response.json(), "attachment");
    },
    /** The API route that answers with a 302 to a short-lived signed link; opened as a navigation. */
    attachmentPath(attachmentId: string): string {
        return `/support/attachments/${encodeURIComponent(attachmentId)}`;
    },
    createTicket(requestId: string, input: TicketDraftInput, signal?: AbortSignal) {
        return write(ticketSchema, "ticket", { path: "/support/tickets", body: { request_id: requestId, ...input }, signal });
    },
    async listTickets(input: { status?: TicketStatus; scope?: "mine" | "team"; cursor?: string | null }, signal?: AbortSignal): Promise<TicketPage> {
        const query: Record<string, string> = {};
        if (input.status) query.status = input.status;
        if (input.scope) query.scope = input.scope;
        if (input.cursor) query.cursor = input.cursor;
        return parse(ticketPageSchema, await api.request({ path: "/support/tickets", method: "GET", query, signal }), "ticket list");
    },
    async getTicket(ticketId: string, signal?: AbortSignal): Promise<Ticket> {
        return parse(ticketSchema, await api.request({ path: `/support/tickets/${encodeURIComponent(ticketId)}`, method: "GET", signal }), "ticket");
    },
    replyToTicket(ticketId: string, requestId: string, body: string, attachmentIds: string[], signal?: AbortSignal) {
        return write(ticketMessageSchema, "reply", { path: `/support/tickets/${encodeURIComponent(ticketId)}/messages`, body: { request_id: requestId, body, attachment_ids: attachmentIds }, signal });
    },
    resolveTicket(ticketId: string, requestId: string, signal?: AbortSignal) {
        return write(ticketSchema, "ticket", { path: `/support/tickets/${encodeURIComponent(ticketId)}/resolve`, body: { request_id: requestId }, signal });
    },
    reopenTicket(ticketId: string, requestId: string, signal?: AbortSignal) {
        return write(ticketSchema, "ticket", { path: `/support/tickets/${encodeURIComponent(ticketId)}/reopen`, body: { request_id: requestId }, signal });
    },
    rateTicket(ticketId: string, requestId: string, score: number, comment: string | undefined, signal?: AbortSignal) {
        const body: Record<string, unknown> = { request_id: requestId, score };
        if (comment && comment.trim()) body.comment = comment.trim();
        return write(ticketSchema, "ticket", { path: `/support/tickets/${encodeURIComponent(ticketId)}/csat`, body, signal });
    },
    async unread(signal?: AbortSignal): Promise<z.infer<typeof unreadSchema>> {
        return parse(unreadSchema, await api.request({ path: "/support/unread", method: "GET", signal, timeoutMs: 12_000 }), "unread count");
    },
};
