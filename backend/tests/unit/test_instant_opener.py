"""Unit tests for the caller-first instant opener's bare-greeting gate."""
from app.domain.services.voice_pipeline.instant_opener import is_bare_greeting


def test_bare_greetings_match():
    for t in ("Hello?", "Hi", "hello hello", "Yeah?", "Good morning", "Hiya",
              "Hello, who is this", "Yes speaking"):
        assert is_bare_greeting(t), t


def test_content_questions_do_not_match():
    for t in ("Who's calling from where exactly?", "What do you want",
              "Is this about the invoice?", "Hello, Acme Roofing?",
              "This is the Vodafone voice mail"):
        assert not is_bare_greeting(t), t


def test_empty_and_long():
    assert not is_bare_greeting("")
    assert not is_bare_greeting("hello there my good friend how are you")
