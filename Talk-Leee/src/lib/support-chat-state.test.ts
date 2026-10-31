import assert from "node:assert/strict";
import { test } from "node:test";
import { chatReducer, draftFromMessages, initialChatState, type ChatEvent, type ChatState } from "@/lib/support-chat-state";
import type { BotMessage, Escalation } from "@/lib/support-api";

const at = "2026-10-30T10:00:00+00:00";
const bot = (content: string, id = "m-bot"): BotMessage => ({ id, role: "assistant", content, sources: [], created_at: at });
const escalation = (existing: Escalation["existing_ticket"] = null): Escalation => ({
    reason: "no_answer_found", priority_hint: "normal",
    draft: { subject: "Calls fail", category: "calls_campaigns", description: "Problem:\n- Calls fail" }, existing_ticket: existing,
});

function run(events: ChatEvent[], from: ChatState = initialChatState): ChatState {
    return events.reduce(chatReducer, from);
}

const started: ChatEvent[] = [
    { type: "start", requestId: "r-start" },
    { type: "started", conversationId: "c1", greeting: "Hi", suggestions: ["Billing"], at },
];

test("idle → chatting: start names a pending write and started records the greeting", () => {
    const pending = run([started[0]!]);
    assert.equal(pending.phase, "chatting");
    assert.deepEqual(pending.pending, { kind: "start", requestId: "r-start" });
    const state = run(started);
    assert.equal(state.conversationId, "c1");
    assert.equal(state.pending, null);
    assert.deepEqual(state.suggestions, ["Billing"]);
    assert.equal(state.messages[0]?.content, "Hi");
    assert.equal(run([{ type: "start", requestId: "again" }], state).pending, null, "a second start while chatting is ignored");
});

test("chatting → awaiting_feedback on a resolution prompt, back to chatting without one", () => {
    const sent = run([...started, { type: "send", requestId: "r-1", content: "help", at }]);
    assert.equal(sent.messages.at(-1)?.role, "user");
    assert.deepEqual(sent.pending, { kind: "message", requestId: "r-1", content: "help" });
    const prompted = chatReducer(sent, { type: "bot_reply", message: bot("Try this"), resolutionPrompt: true, escalation: null });
    assert.equal(prompted.phase, "awaiting_feedback");
    assert.equal(prompted.pending, null);
    const plain = chatReducer(sent, { type: "bot_reply", message: bot("Tell me more"), resolutionPrompt: false, escalation: null });
    assert.equal(plain.phase, "chatting");
});

test("a second send while one is pending is ignored", () => {
    const sent = run([...started, { type: "send", requestId: "r-1", content: "help", at }]);
    const again = chatReducer(sent, { type: "send", requestId: "r-2", content: "hello?", at });
    assert.equal(again.pending?.requestId, "r-1");
    assert.equal(again.messages.length, sent.messages.length);
});

test("awaiting_feedback: yes → submitted with the solved outcome, no → follow-up or escalation", () => {
    const waiting = run([...started, { type: "send", requestId: "r-1", content: "help", at },
        { type: "bot_reply", message: bot("Try this"), resolutionPrompt: true, escalation: null }]);
    const yes = run([{ type: "feedback", requestId: "r-f", solved: true }, { type: "feedback_result", solved: true, message: null, escalation: null }], waiting);
    assert.equal(yes.phase, "submitted");
    assert.deepEqual(yes.outcome, { kind: "solved" });
    const no = run([{ type: "feedback", requestId: "r-f", solved: false }, { type: "feedback_result", solved: false, message: bot("Tell me more"), escalation: null }], waiting);
    assert.equal(no.phase, "chatting");
    assert.equal(no.messages.at(-1)?.content, "Tell me more");
    const twice = run([{ type: "feedback", requestId: "r-f", solved: false }, { type: "feedback_result", solved: false, message: bot("Sorry"), escalation: escalation() }], waiting);
    assert.equal(twice.phase, "drafting_ticket");
    assert.equal(twice.existingChoice, "new");
    assert.equal(chatReducer(run(started), { type: "feedback", requestId: "x", solved: true }).pending, null, "feedback outside the prompt is ignored");
});

test("escalation from a bot reply and from Talk to a person both reach drafting_ticket", () => {
    const sent = run([...started, { type: "send", requestId: "r-1", content: "refund", at }]);
    const fromReply = chatReducer(sent, { type: "bot_reply", message: bot("This needs a person"), resolutionPrompt: true, escalation: escalation() });
    assert.equal(fromReply.phase, "drafting_ticket", "escalation wins over the resolution prompt");
    const asked = run([{ type: "escalate", requestId: "r-e" }], run(started));
    assert.deepEqual(asked.pending, { kind: "escalate", requestId: "r-e" });
    const drafted = chatReducer(asked, { type: "escalated", escalation: escalation() });
    assert.equal(drafted.phase, "drafting_ticket");
    assert.equal(drafted.pending, null);
});

test("an existing ticket must be chosen before submitting; append and create name the right pending write", () => {
    const existing = { id: "t-old", number: "SUP-000009", subject: "Old", status: "open" as const };
    const drafted = chatReducer(run(started), { type: "escalated", escalation: escalation(existing) });
    assert.equal(drafted.existingChoice, null);
    const append = run([{ type: "choose_existing", choice: "append" }, { type: "append", requestId: "r-a", ticket: existing }], drafted);
    assert.equal(append.phase, "submitting");
    assert.deepEqual(append.pending, { kind: "append_to_ticket", requestId: "r-a", ticketId: "t-old" });
    const done = chatReducer(append, { type: "appended", ticketId: "t-old", number: "SUP-000009" });
    assert.equal(done.phase, "submitted");
    assert.deepEqual(done.outcome, { kind: "appended", ticketId: "t-old", number: "SUP-000009" });
    const fresh = run([{ type: "choose_existing", choice: "new" }, { type: "submit", requestId: "r-s" }], drafted);
    assert.deepEqual(fresh.pending, { kind: "create_ticket", requestId: "r-s" });
    const created = chatReducer(fresh, { type: "ticket_created", ticketId: "t-new", number: "SUP-000010" });
    assert.equal(created.phase, "submitted");
    assert.deepEqual(created.outcome, { kind: "ticket", ticketId: "t-new", number: "SUP-000010" });
});

test("a failed write keeps its request_id and retry re-enters the same phase with the same pending write", () => {
    const sent = run([...started, { type: "send", requestId: "r-1", content: "help", at }]);
    const failed = chatReducer(sent, { type: "fail", message: "network" });
    assert.equal(failed.phase, "failed");
    assert.equal(failed.error, "network");
    assert.equal(failed.resumePhase, "chatting");
    assert.deepEqual(failed.pending, { kind: "message", requestId: "r-1", content: "help" });
    const retried = chatReducer(failed, { type: "retry" });
    assert.equal(retried.phase, "chatting");
    assert.equal(retried.error, null);
    assert.deepEqual(retried.pending, failed.pending, "retry reuses the same request_id");
    const failedTwice = chatReducer(chatReducer(retried, { type: "fail", message: "again" }), { type: "fail", message: "and again" });
    assert.equal(failedTwice.resumePhase, "chatting", "a repeated failure keeps the original resume phase");
    assert.equal(chatReducer(run(started), { type: "retry" }).phase, "chatting", "retry outside failed is a no-op");
});

test("a failed ticket submission retries as submitting with the same id", () => {
    const drafted = chatReducer(run(started), { type: "escalated", escalation: escalation() });
    const submitting = chatReducer(drafted, { type: "submit", requestId: "r-s" });
    const failed = chatReducer(submitting, { type: "fail", message: "lost" });
    const retried = chatReducer(failed, { type: "retry" });
    assert.equal(retried.phase, "submitting");
    assert.equal(retried.pending?.requestId, "r-s");
});

test("Create a ticket instead drops the failed write and drafts from the customer's own words", () => {
    const failed = run([...started, { type: "send", requestId: "r-1", content: "My caller ID is wrong", at }, { type: "fail", message: "timeout" }]);
    const drafted = chatReducer(failed, { type: "draft_without_bot" });
    assert.equal(drafted.phase, "drafting_ticket");
    assert.equal(drafted.pending, null, "the failed message write is not replaced by another request");
    assert.equal(drafted.escalation?.draft.subject, "My caller ID is wrong");
    assert.match(drafted.escalation?.draft.description ?? "", /- My caller ID is wrong/);
    assert.equal(draftFromMessages([]).draft.subject, "Help needed");
});

test("resume rebuilds the state from the server's conversation", () => {
    const conversation = { conversation_id: "c9", status: "active" as const, messages: [bot("Hi", "m1")], escalation: null };
    const active = chatReducer(initialChatState, { type: "resumed", conversation, suggestions: ["A"] });
    assert.equal(active.phase, "chatting");
    assert.equal(active.conversationId, "c9");
    assert.equal(active.messages.length, 1);
    const drafting = chatReducer(initialChatState, { type: "resumed", conversation: { ...conversation, escalation: escalation() }, suggestions: [] });
    assert.equal(drafting.phase, "drafting_ticket");
    const solved = chatReducer(initialChatState, { type: "resumed", conversation: { ...conversation, status: "resolved_by_bot" }, suggestions: [] });
    assert.deepEqual(solved.outcome, { kind: "solved" });
    const ended = chatReducer(initialChatState, { type: "resumed", conversation: { ...conversation, status: "escalated" }, suggestions: [] });
    assert.equal(ended.phase, "idle", "an escalated conversation is finished; the next open starts a new one");
    assert.deepEqual(chatReducer(ended, { type: "reset" }), initialChatState);
});
