import assert from "node:assert/strict";
import { test } from "node:test";
import { draftFromEscalation, ticketDraftPayload, validateTicketDraft, type TicketDraft } from "@/lib/support-ticket-draft";
import type { SupportConfig } from "@/lib/support-api";

const config: SupportConfig = {
    enabled: true, bot_enabled: true, permissions: { report: true, read_team: false },
    categories: [{ id: "billing", label: "Billing and payments" }, { id: "other", label: "Something else" }],
    suggestions: [], attachments: { max_mb: 10, max_per_message: 5, types: ["png"] },
};
const draft: TicketDraft = { subject: "Calls fail", category: "billing", description: "Every call drops after ten seconds.", relatedCampaignId: null, relatedCallId: null };

test("a well-formed draft has no errors", () => {
    assert.deepEqual(validateTicketDraft(draft, config), {});
});

test("subject rules: length, one line, no control characters", () => {
    assert.match(validateTicketDraft({ ...draft, subject: "ab" }, config).subject ?? "", /at least 3/);
    assert.match(validateTicketDraft({ ...draft, subject: "x".repeat(151) }, config).subject ?? "", /at most 150/);
    assert.match(validateTicketDraft({ ...draft, subject: "two\nlines" }, config).subject ?? "", /one line/);
    assert.match(validateTicketDraft({ ...draft, subject: "bad\u0007bell" }, config).subject ?? "", /cannot be sent/);
    assert.equal(validateTicketDraft({ ...draft, subject: "  padded subject  " }, config).subject, undefined);
});

test("description rules: length and control characters, with CRLF normalised first", () => {
    assert.match(validateTicketDraft({ ...draft, description: "short" }, config).description ?? "", /at least 10/);
    assert.match(validateTicketDraft({ ...draft, description: "x".repeat(5001) }, config).description ?? "", /at most 5000/);
    assert.equal(validateTicketDraft({ ...draft, description: "line one\r\nline two here" }, config).description, undefined);
    assert.match(validateTicketDraft({ ...draft, description: "bad\u0000null byte here" }, config).description ?? "", /cannot be sent/);
});

test("the category must be one the config offers", () => {
    assert.match(validateTicketDraft({ ...draft, category: "refunds" }, config).category ?? "", /Choose a category/);
});

test("the escalation draft is prefilled, with an unknown category replaced by the first offered", () => {
    const prefilled = draftFromEscalation({
        reason: "bot_proposed", priority_hint: "high",
        draft: { subject: "S", category: "ai_agent_voices", description: "D", related_campaign_id: "camp-1" }, existing_ticket: null,
    }, config);
    assert.equal(prefilled.category, "billing");
    assert.equal(prefilled.relatedCampaignId, "camp-1");
    assert.equal(prefilled.relatedCallId, null);
    assert.equal(draftFromEscalation({ reason: "x", priority_hint: "low", draft: { subject: "S", category: "other", description: "D" }, existing_ticket: null }, config).category, "other");
});

test("the payload is trimmed and omits what is not set", () => {
    const payload = ticketDraftPayload({ ...draft, subject: "  Calls   fail ", description: " a\r\nb ", relatedCallId: "call-1" }, "conv-1", ["att-1"]);
    assert.deepEqual(payload, { conversation_id: "conv-1", subject: "Calls fail", category: "billing", description: "a\nb", related_call_id: "call-1", attachment_ids: ["att-1"] });
    assert.equal("conversation_id" in ticketDraftPayload(draft, null, []), false);
    assert.equal("related_campaign_id" in ticketDraftPayload(draft, null, []), false);
});
