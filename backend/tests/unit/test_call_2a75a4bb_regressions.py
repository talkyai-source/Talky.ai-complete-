"""Browser test call 2a75a4bb (2026-09-30, "Estimation new"), replayed.

The full conversation was sent to the model every turn (prompt tokens grew
12,314 -> 12,789), yet after the call-back "yes" the agent asked "Is it
something you're currently tendering for, or a project you've already
secured?" again -- asked and answered at the start -- and the caller said
"You already asked me this." That turn was served by the fallback model.

The "already answered" note did not list it: the caller's answer was heard
as the single word "zero", and one-word answers were not counted; and the
note kept only the last four answered questions, so early ones fell off.
"""
from __future__ import annotations

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.conversation_guards import (
    answered_note,
    answered_questions,
    repeats_answered_question,
)


def _history():
    def a(text):
        return Message(role=MessageRole.ASSISTANT, content=text)

    def u(text):
        return Message(role=MessageRole.USER, content=text)

    return [
        a("Hi there."), u("Hello."),
        a("Hi, this is Sarah, the AI assistant for All State Estimation. I’ll keep this "
          "really quick. Do you have any upcoming projects you might need an estimate for?"),
        u("Yes, there are plenty of it."),
        a("Great. Is it something you're currently tendering for, or a project you’ve already secured?"),
        u("zero"),
        a("Sounds like none secured yet—what type of project is it?"),
        u("There are plenty of heat construction and plenty of it."),
        a("Got it. When do you need the estimate by?"),
        u("known after months."),
        a("Perfect. What’s the best email address for you?"),
        u("u k six seven zero one three at g mail dot com."),
        a("Let me confirm that — u k six seven zero one three at g mail dot com. Is that correct?"),
        u("Yes."),
        a("Perfect. Would you prefer someone to call you back as well?"),
        u("yes"),
    ]


def test_the_tendering_question_counts_as_answered_and_is_caught():
    answered = answered_questions(_history())
    assert repeats_answered_question(
        "Is it something you’re currently tendering for, or a project you’ve already secured?",
        answered,
    ) == "zero"
    note = answered_note(answered)
    assert "tendering" in note
    # The first question is still remembered at the end of the call.
    assert "upcoming projects" in note


def test_a_greeting_or_pardon_is_not_an_answer():
    def a(text):
        return Message(role=MessageRole.ASSISTANT, content=text)

    def u(text):
        return Message(role=MessageRole.USER, content=text)

    answered = answered_questions([
        a("Do you have any upcoming projects you might need an estimate for?"), u("Hello?"),
        a("Is it something you are tendering for or already secured?"), u("Sorry, what?"),
    ])
    assert answered == []
