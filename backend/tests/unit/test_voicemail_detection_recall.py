"""QA F10-06: answering-machine detection over a labelled set of first utterances.

QA ran the phrase gate over 60 labelled first utterances (30 machines, 30
people): it never hung up on a person (precision 100%) but caught only 21 of
30 machines. It missed mailbox-full, not-in-service and "subscriber not
available" network messages, "text me or leave a voicemail", O2 messaging, an
office-closed message, "please hold while we try to connect you", and two
call-screening services including Google's. Floor: precision 100%, recall at
least 90%. Network messages are recorded as the carrier's own cause code
would be, never as voicemail.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.machine_detection import (
    assess_machine_text,
    handle_machine_interim,
)

MACHINES = [
    "Your call has been forwarded to an automated voice messaging system.",
    "The person you are trying to reach is not available. At the tone, please record your message.",
    "Hi, you've reached the voicemail of John Smith, please leave a message after the beep.",
    "Welcome to the Vodafone voicemail service.",
    "Hi, this is the EE voicemail for oh seven seven double oh.",
    "Sorry I missed your call, please leave me a message and your phone number.",
    "Hello, you've reached Acme Plumbing, please leave your name and number.",
    "The person at extension two oh four is not available.",
    "Google Voice subscriber, please leave a message after the tone.",
    "Please record your message after the tone. When you have finished, hang up.",
    "Hi, we can't get to the phone right now, leave us a message.",
    "Thanks for calling, at the sound of the tone please leave your message.",
    "Your call has been forwarded to voicemail.",
    "Hi, it's Dave, leave a message.",
    "To re-record your message, press two.",
    "The person you have called is unavailable, please leave a message.",
    # QA's misses
    "The mailbox is full and cannot accept any more messages. Goodbye.",
    "The number you have dialled is not in service. Please check the number and try again.",
    "The subscriber you have called is not available, please try later.",
    "Hey, it's Priya, I can't pick up, text me or leave a voicemail.",
    "Welcome to O2 messaging. The person you are calling is not available.",
    "Thank you for calling Hill Dental. Our office is currently closed.",
    "Please hold while we try to connect you.",
    "Hi, the person you're calling is using a screening service from Google, and will get "
    "a copy of this conversation. Go ahead and say your name and why you're calling.",
    "Please state your name and the reason for your call, and I'll see if this person is available.",
    "The number you have dialed has not been recognized.",
    "The mobile phone you are calling is switched off.",
    "This voicemail has not been set up yet.",
    "Our offices are now closed. Please call back during business hours.",
    "The subscriber you are calling cannot be reached at the moment.",
]

PEOPLE = [
    "Hello?",
    "Hi, who's this?",
    "Yes, speaking.",
    "Hello, John speaking.",
    "Sorry, I can't talk right now, can you call back later?",
    "Who is calling, please?",
    "Hello, this is Sarah from accounts.",
    "Yeah, what do you want?",
    "Hi there, how can I help you?",
    "Good morning, Hill Dental, Emma speaking.",
    "Hello? I can't hear you very well.",
    "Yes, this is she.",
    "Hi, is this about my order?",
    "Sorry, who did you want?",
    "Hang on, let me turn the TV down.",
    "Hello, hello?",
    "Speaking, who's this?",
    "Oh hi, I was expecting your call.",
    "He's not here at the moment, can I take a message?",
    "She's busy, can you call back in an hour?",
    "Can you hold on a second?",
    "Please hold, I'll get him for you.",
    "Who gave you this number?",
    "I'm driving, make it quick.",
    "Hello, Acme Plumbing, how can I help?",
    "Hi, yes, I got a missed call from this number.",
    "Hello, is anyone there?",
    "Not interested, thanks.",
    "Yes? Who am I speaking to?",
    "Hi, sorry, I'm in a meeting, what's it about?",
]


def _detected(text: str) -> bool:
    return assess_machine_text(text, turn_index=0, screening_seen=False) != "none"


def test_the_labelled_set_meets_the_precision_floor_and_recall_target():
    assert len(MACHINES) == 30 and len(PEOPLE) == 30
    hung_up_on_people = [t for t in PEOPLE if _detected(t)]
    missed = [t for t in MACHINES if not _detected(t)]
    assert hung_up_on_people == []
    # "Please hold while we try to connect you" is left alone on purpose: a
    # live answer can follow it, and hanging up there would break precision.
    assert missed == ["Please hold while we try to connect you."]
    assert (len(MACHINES) - len(missed)) / len(MACHINES) >= 0.9


@pytest.mark.parametrize("text,verdict", [
    ("The number you have dialled is not in service.", "network_not_in_service"),
    ("The number you have dialed has not been recognized.", "network_not_in_service"),
    ("The subscriber you have called is not available, please try later.", "network_unreachable"),
    ("The mobile phone you are calling is switched off.", "network_unreachable"),
    ("Hi, the person you're calling is using a screening service from Google.", "screening"),
    ("The mailbox is full and cannot accept any more messages.", "voicemail"),
])
def test_network_messages_are_told_apart_from_voicemail(text, verdict):
    assert assess_machine_text(text, turn_index=0, screening_seen=False) == verdict


@pytest.mark.asyncio
@pytest.mark.parametrize("text,outcome", [
    ("The number you have dialled is not in service.", "invalid"),
    ("The subscriber you have called is not available, please try later.", "no_answer"),
])
async def test_a_network_message_hangs_up_without_marking_voicemail(text, outcome):
    session = SimpleNamespace(turn_id=0, _call_direction="outbound")
    gateway = SimpleNamespace(hangup_call=AsyncMock(return_value=True))
    assert await handle_machine_interim("call-f1006", session, text, media_gateway=gateway) is True
    assert session._amd_network_outcome == outcome
    assert not getattr(session, "_amd_voicemail", False)
    gateway.hangup_call.assert_awaited_once()


@pytest.mark.parametrize("flag,expected", [("invalid", "invalid"), ("no_answer", "no_answer")])
def test_network_outcome_is_recorded_as_the_carrier_cause_would_be(flag, expected):
    """Q.850 1 (unallocated) is INVALID and Q.850 20 (phone off) is NO_ANSWER
    in the cause map; the spoken network messages land on the same outcomes."""
    from app.domain.services.telephony.outcome_resolver import resolve_call_outcome

    call_session = SimpleNamespace(_amd_network_outcome=flag, conversation_history=[])
    voice_session = SimpleNamespace(call_session=call_session)
    assert resolve_call_outcome(voice_session).value == expected
