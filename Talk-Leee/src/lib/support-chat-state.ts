import type { BotMessage, Conversation, Escalation, ExistingTicket } from "@/lib/support-api";

/*
 * The support drawer's chat logic as a pure reducer. The component only
 * dispatches events and executes whatever `pending` write the state names;
 * every request_id is created by the caller and carried in the event, so a
 * retry re-executes the same pending write with the same id.
 *
 * idle → chatting → awaiting_feedback → drafting_ticket → submitting → submitted | failed
 */

export type ChatPhase = "idle" | "chatting" | "awaiting_feedback" | "drafting_ticket" | "submitting" | "submitted" | "failed";

export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; sources: BotMessage["sources"]; createdAt: string };

export type PendingWrite =
    | { kind: "start"; requestId: string }
    | { kind: "message"; requestId: string; content: string }
    | { kind: "feedback"; requestId: string; solved: boolean }
    | { kind: "escalate"; requestId: string }
    | { kind: "create_ticket"; requestId: string }
    | { kind: "append_to_ticket"; requestId: string; ticketId: string };

export type ChatOutcome =
    | { kind: "solved" }
    | { kind: "ticket"; ticketId: string; number: string }
    | { kind: "appended"; ticketId: string; number: string };

export type ChatState = {
    phase: ChatPhase;
    conversationId: string | null;
    messages: ChatMessage[];
    suggestions: string[];
    pending: PendingWrite | null;
    /** The phase to return to when a failed write is retried. */
    resumePhase: ChatPhase | null;
    escalation: Escalation | null;
    /** Chosen when the server reports an existing open ticket: append there or draft a new one. */
    existingChoice: "append" | "new" | null;
    outcome: ChatOutcome | null;
    error: string | null;
};

export type ChatEvent =
    | { type: "start"; requestId: string }
    | { type: "started"; conversationId: string; greeting: string; suggestions: string[]; at: string }
    | { type: "resumed"; conversation: Conversation; suggestions: string[] }
    | { type: "send"; requestId: string; content: string; at: string }
    | { type: "bot_reply"; message: BotMessage; resolutionPrompt: boolean; escalation: Escalation | null }
    | { type: "feedback"; requestId: string; solved: boolean }
    | { type: "feedback_result"; solved: boolean; message: BotMessage | null; escalation: Escalation | null }
    | { type: "escalate"; requestId: string }
    | { type: "escalated"; escalation: Escalation }
    | { type: "choose_existing"; choice: "append" | "new" }
    | { type: "submit"; requestId: string }
    | { type: "append"; requestId: string; ticket: ExistingTicket }
    | { type: "ticket_created"; ticketId: string; number: string }
    | { type: "appended"; ticketId: string; number: string }
    | { type: "fail"; message: string }
    | { type: "retry" }
    | { type: "draft_without_bot" }
    | { type: "reset" };

export const initialChatState: ChatState = {
    phase: "idle", conversationId: null, messages: [], suggestions: [], pending: null, resumePhase: null,
    escalation: null, existingChoice: null, outcome: null, error: null,
};

export function toChatMessage(message: BotMessage): ChatMessage {
    return { id: message.id, role: message.role, content: message.content, sources: message.sources, createdAt: message.created_at };
}

function withEscalation(state: ChatState, escalation: Escalation): ChatState {
    return { ...state, phase: "drafting_ticket", escalation, existingChoice: escalation.existing_ticket ? null : "new", pending: null, error: null };
}

/** A ticket draft when the assistant never produced one: the customer's own words. */
export function draftFromMessages(messages: ChatMessage[]): Escalation {
    const mine = messages.filter((message) => message.role === "user").map((message) => message.content.trim()).filter(Boolean);
    const subject = (mine[0] ?? "Help needed").replace(/\s+/g, " ").slice(0, 150);
    const description = mine.length ? `Problem:\n${mine.map((line) => `- ${line}`).join("\n")}` : "Problem:\n- The customer asked for help from a person.";
    return { reason: "customer_requested", priority_hint: "normal", draft: { subject, category: "other", description }, existing_ticket: null };
}

export function chatReducer(state: ChatState, event: ChatEvent): ChatState {
    switch (event.type) {
        case "start":
            if (state.phase !== "idle") return state;
            return { ...initialChatState, phase: "chatting", pending: { kind: "start", requestId: event.requestId } };
        case "started":
            return {
                ...state, phase: "chatting", conversationId: event.conversationId, pending: null, error: null, resumePhase: null,
                suggestions: event.suggestions,
                messages: [{ id: `greeting:${event.conversationId}`, role: "assistant", content: event.greeting, sources: [], createdAt: event.at }],
            };
        case "resumed": {
            const conversation = event.conversation;
            const base: ChatState = {
                ...initialChatState, conversationId: conversation.conversation_id, messages: conversation.messages.map(toChatMessage),
                suggestions: event.suggestions,
            };
            if (conversation.status === "resolved_by_bot") return { ...base, phase: "submitted", outcome: { kind: "solved" } };
            if (conversation.status !== "active") return { ...base, phase: "idle" };
            if (conversation.escalation) return withEscalation(base, conversation.escalation);
            return { ...base, phase: "chatting" };
        }
        case "send":
            if (state.phase !== "chatting" && state.phase !== "awaiting_feedback") return state;
            if (state.pending) return state;
            return {
                ...state, phase: "chatting", error: null,
                messages: [...state.messages, { id: `local:${event.requestId}`, role: "user", content: event.content, sources: [], createdAt: event.at }],
                pending: { kind: "message", requestId: event.requestId, content: event.content },
            };
        case "bot_reply": {
            const messages = [...state.messages, toChatMessage(event.message)];
            if (event.escalation) return withEscalation({ ...state, messages }, event.escalation);
            return { ...state, messages, pending: null, error: null, resumePhase: null, phase: event.resolutionPrompt ? "awaiting_feedback" : "chatting" };
        }
        case "feedback":
            if (state.phase !== "awaiting_feedback" || state.pending) return state;
            return { ...state, pending: { kind: "feedback", requestId: event.requestId, solved: event.solved }, error: null };
        case "feedback_result": {
            const messages = event.message ? [...state.messages, toChatMessage(event.message)] : state.messages;
            if (event.solved) return { ...state, messages, phase: "submitted", outcome: { kind: "solved" }, pending: null, error: null, resumePhase: null };
            if (event.escalation) return withEscalation({ ...state, messages }, event.escalation);
            return { ...state, messages, phase: "chatting", pending: null, error: null, resumePhase: null };
        }
        case "escalate":
            if (state.phase === "submitting" || state.phase === "submitted" || state.pending) return state;
            return { ...state, pending: { kind: "escalate", requestId: event.requestId }, error: null };
        case "escalated":
            return withEscalation(state, event.escalation);
        case "choose_existing":
            if (state.phase !== "drafting_ticket") return state;
            return { ...state, existingChoice: event.choice };
        case "submit":
            if (state.phase !== "drafting_ticket" || state.pending) return state;
            return { ...state, phase: "submitting", pending: { kind: "create_ticket", requestId: event.requestId }, error: null };
        case "append":
            if (state.phase !== "drafting_ticket" || state.pending) return state;
            return { ...state, phase: "submitting", pending: { kind: "append_to_ticket", requestId: event.requestId, ticketId: event.ticket.id }, error: null };
        case "ticket_created":
            return { ...state, phase: "submitted", outcome: { kind: "ticket", ticketId: event.ticketId, number: event.number }, pending: null, error: null, resumePhase: null };
        case "appended":
            return { ...state, phase: "submitted", outcome: { kind: "appended", ticketId: event.ticketId, number: event.number }, pending: null, error: null, resumePhase: null };
        case "fail":
            // The pending write and its request_id survive so Retry sends the same thing.
            return { ...state, phase: "failed", resumePhase: state.phase === "failed" ? state.resumePhase : state.phase, error: event.message };
        case "retry":
            if (state.phase !== "failed" || !state.pending || !state.resumePhase) return state;
            return { ...state, phase: state.resumePhase, error: null };
        case "draft_without_bot":
            // "Create a ticket instead": the failed write is dropped, never replaced by a different request.
            return withEscalation({ ...state, pending: null, resumePhase: null }, state.escalation ?? draftFromMessages(state.messages));
        case "reset":
            return initialChatState;
        default:
            return state;
    }
}
