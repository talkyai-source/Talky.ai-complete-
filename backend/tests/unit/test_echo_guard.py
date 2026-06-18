"""Tests for the self-echo guard (echo_guard.strip_self_echo)."""
from __future__ import annotations

from app.services.scripts.echo_guard import strip_self_echo


AGENT = "Brilliant. Just so I get a handle on your setup, what kind of business are you running?"


def test_echo_plus_real_reply_keeps_only_the_reply():
    # The exact production case: agent's sentence echoed back + the real answer.
    user = AGENT + " I'm running a restaurant."
    assert strip_self_echo(user, AGENT) == "I'm running a restaurant."


def test_pure_echo_returns_blank():
    assert strip_self_echo(AGENT, AGENT) == ""


def test_short_backchannels_pass_through():
    assert strip_self_echo("yeah", AGENT) == "yeah"
    assert strip_self_echo("yes I am", AGENT) == "yes I am"
    assert strip_self_echo("no not really", AGENT) == "no not really"


def test_unrelated_reply_unchanged():
    assert (
        strip_self_echo("I run a dental clinic actually downtown", AGENT)
        == "I run a dental clinic actually downtown"
    )


def test_short_legit_overlap_not_stripped():
    # Caller reuses 2 of the agent's words — below the run threshold, keep it.
    agent = "are you using Stripe for payments right now"
    assert strip_self_echo("yes using Stripe", agent) == "yes using Stripe"


def test_no_agent_text_is_noop():
    assert strip_self_echo("hello there my friend how are you", "") == "hello there my friend how are you"


def test_empty_user_text_is_noop():
    assert strip_self_echo("", AGENT) == ""
