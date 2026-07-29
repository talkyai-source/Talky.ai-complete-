"""Caller PII must never reach a log handler verbatim.

The voice pipeline logs the caller's utterance on nearly every branch
(``transcript_received``, backchannel, self-echo, ``turn_end``, and a
slow-turn WARNING that Sentry turns into a breadcrumb leaving the box).
Callers say health and financial details out loud, and nothing in this stack
applies a retention limit to log content — so the redactor in
``app.core.log_redact`` is installed process-wide and scrubs the record
BEFORE any handler, formatter or Sentry integration sees it.

These tests assert on the ``LogRecord`` as a handler receives it, which is the
actual egress point (extras are not rendered by today's formatters, but they
are carried into Sentry and into any structured/JSON handler added later).
"""
from __future__ import annotations

import logging

import pytest

from app.core import log_redact
from app.core.log_redact import (
    REDACTION_FAILED,
    install_pii_log_redaction,
    set_pii_redaction_enabled,
    uninstall_pii_log_redaction,
)

EMAIL = "john.smith1990@example.com"
PHONE = "+44 7911 123456"
CARD = "4111111111111111"
CARD_SPACED = "4111 1111 1111 1111"
UTTERANCE = (
    "Yeah so I was diagnosed with type two diabetes last March and my "
    "mortgage with Barclays is about four hundred thousand outstanding, "
    "you can email me at john.smith1990@example.com"
)


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def caplog_records():
    """A logger + handler pair with the redactor guaranteed installed."""
    install_pii_log_redaction()
    set_pii_redaction_enabled(True)
    logger = logging.getLogger("tests.pii_redaction")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)
        set_pii_redaction_enabled(None)


def _egress(record: logging.LogRecord) -> str:
    """Everything a handler could possibly write out for this record.

    Formatted message plus every extra attribute — i.e. what journald, a JSON
    handler, or a Sentry breadcrumb would carry.
    """
    parts = [record.getMessage()]
    standard = log_redact._STANDARD_RECORD_ATTRS
    for key, value in sorted(record.__dict__.items()):
        if key in standard:
            continue
        parts.append(f"{key}={value!r}")
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# The four required redactions
# ---------------------------------------------------------------------------

def test_email_address_is_redacted(caplog_records):
    logger, handler = caplog_records
    logger.info("lead_captured email=%s", EMAIL)
    logger.info("lead_captured", extra={"lead_email": EMAIL})

    for record in handler.records:
        out = _egress(record)
        assert EMAIL not in out
        assert "john.smith1990" not in out
        # Domain survives so operators can still tell STT heard it right.
        assert "@example.com" in out


def test_phone_number_is_redacted(caplog_records):
    logger, handler = caplog_records
    logger.info("callback_number=%s", PHONE)

    out = _egress(handler.records[0])
    assert PHONE not in out
    assert "7911" not in out
    assert "redacted digits=" in out
    assert "..56" in out  # last two digits kept for correlation


def test_sixteen_digit_run_is_redacted(caplog_records):
    logger, handler = caplog_records
    logger.info("caller said %s and %s", CARD, CARD_SPACED)

    out = _egress(handler.records[0])
    assert CARD not in out
    assert CARD_SPACED not in out
    assert "1111 1111" not in out
    assert out.count("redacted digits=16") == 2


def test_ssn_shape_is_redacted_but_iso_dates_are_not(caplog_records):
    logger, handler = caplog_records
    logger.info("caller gave %s on 2026-07-29", "078-05-1120")
    out = handler.records[0].getMessage()
    assert "078-05-1120" not in out
    assert "2026-07-29" in out  # 4-2-2 is a date, not an SSN


def test_long_utterance_is_redacted_in_extra_and_in_args(caplog_records):
    logger, handler = caplog_records
    # The two real shapes from the voice pipeline.
    logger.info(
        "transcript_received",
        extra={"call_id": "abc123def456", "turn_id": 3, "text": UTTERANCE},
    )
    logger.info("backchannel_suppressed transcript=%r call=%s", UTTERANCE, "abc123def456")

    for record in handler.records:
        out = _egress(record)
        assert "diabetes" not in out
        assert "Barclays" not in out
        assert EMAIL not in out
        assert UTTERANCE not in out
        # Length + stable hash survive so turns stay correlatable.
        assert f"chars={len(UTTERANCE)}" in out
        assert "sha=" in out

    # Same utterance -> same hash on both lines (that is the correlation key),
    # and identifiers are untouched.
    first, second = (_egress(r) for r in handler.records)
    sha = first.split("sha=")[1][:8]
    assert f"sha={sha}" in second
    # Identifiers stay fully readable on both lines.
    assert handler.records[0].call_id == "abc123def456"
    assert handler.records[0].turn_id == 3
    assert "abc123def456" in second


# ---------------------------------------------------------------------------
# Debuggability: ordinary log lines must survive untouched
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "msg, args",
    [
        ("turn_complete call=%s turn_id=%d latency_ms=%.1f", ("abc123def456", 4, 812.5)),
        ("voice_slow_turn response_start_ms=%.1f stt_first_ms=%s", (1523.4, "n/a")),
        ("bridge established at 2026-07-29 12:34:56 for channel %s", ("SIP/150002-0000a1b2",)),
        ("agent_end_call call_id=%s — model requested hangup", ("9dddd72d-4f21",)),
    ],
)
def test_normal_log_lines_are_unchanged(caplog_records, msg, args):
    logger, handler = caplog_records
    expected = msg % args
    logger.info(msg, *args)
    assert handler.records[0].getMessage() == expected


def test_format_string_placeholders_are_never_damaged(caplog_records):
    """A %-template is a source literal — rewriting it breaks substitution.

    ``"%s@example.com"`` reads as an email address; scrubbing it would eat the
    placeholder and raise "not all arguments converted" at format time.
    """
    logger, handler = caplog_records
    logger.info("notify %s@example.com about turn %d", "ops", 7)
    assert handler.records[0].getMessage() == "notify ops@example.com about turn 7"


def test_fstring_messages_are_scrubbed(caplog_records):
    logger, handler = caplog_records
    logger.info(f"caller left {EMAIL} and {CARD}")  # noqa: G004 - deliberate
    out = handler.records[0].getMessage()
    assert EMAIL not in out
    assert CARD not in out
    assert "@example.com" in out


def test_timestamps_and_short_numbers_are_not_mangled(caplog_records):
    logger, handler = caplog_records
    logger.info("started=2026-07-29 12:34:56 port=6379 db=0 ms=1234.5 seq=42")
    out = handler.records[0].getMessage()
    assert out == "started=2026-07-29 12:34:56 port=6379 db=0 ms=1234.5 seq=42"


def test_non_text_values_in_sensitive_slots_survive(caplog_records):
    """A number/bool/None cannot be speech — rewriting it is pure damage.

    Regression guard: an early cut of this filter turned Groq's
    ``partial=%s`` usage flag into ``partial=[redacted chars=5 ...]``.
    """
    logger, handler = caplog_records
    logger.info("llm_usage partial=%s text=%s response=%s", False, None, 200)
    assert handler.records[0].getMessage() == "llm_usage partial=False text=None response=200"


# ---------------------------------------------------------------------------
# Non-vacuous: with the filter off, the raw value provably reaches the handler
# ---------------------------------------------------------------------------

def test_redaction_is_non_vacuous(caplog_records):
    logger, handler = caplog_records

    # OFF -> raw caller speech reaches the handler (this is the bug).
    set_pii_redaction_enabled(False)
    logger.info("turn_end", extra={"transcript": UTTERANCE})
    raw = _egress(handler.records[-1])
    assert UTTERANCE in raw
    assert EMAIL in raw

    # ON -> same call site, nothing sensitive left.
    set_pii_redaction_enabled(True)
    logger.info("turn_end", extra={"transcript": UTTERANCE})
    redacted = _egress(handler.records[-1])
    assert UTTERANCE not in redacted
    assert EMAIL not in redacted
    assert "chars=" in redacted


def test_env_var_controls_the_flag(monkeypatch):
    monkeypatch.setenv("LOG_REDACT_PII", "0")
    set_pii_redaction_enabled(None)
    assert log_redact.pii_redaction_enabled() is False

    monkeypatch.setenv("LOG_REDACT_PII", "1")
    set_pii_redaction_enabled(None)
    assert log_redact.pii_redaction_enabled() is True

    # Unset -> ON by default (production must be redacted without opting in).
    monkeypatch.delenv("LOG_REDACT_PII", raising=False)
    set_pii_redaction_enabled(None)
    assert log_redact.pii_redaction_enabled() is True


# ---------------------------------------------------------------------------
# Fail-safe
# ---------------------------------------------------------------------------

def test_redactor_errors_are_swallowed_without_leaking(caplog_records, monkeypatch):
    logger, handler = caplog_records

    def _boom(record):
        raise RuntimeError("redactor exploded")

    monkeypatch.setattr(log_redact, "_redact_record_inplace", _boom)

    # Logging must not raise...
    logger.info("turn_end transcript=%r", UTTERANCE, extra={"text": UTTERANCE})

    out = _egress(handler.records[0])
    # ...and must not fall back to emitting the raw value.
    assert UTTERANCE not in out
    assert "diabetes" not in out
    assert REDACTION_FAILED in out


def test_redactor_never_raises_on_odd_records(caplog_records):
    logger, handler = caplog_records
    logger.info({"not": "a string"})           # non-str msg
    logger.info("no args at all")
    logger.info("%(transcript)s", {"transcript": UTTERANCE})  # mapping-style args
    assert len(handler.records) == 3
    assert UTTERANCE not in _egress(handler.records[2])


def test_non_string_sensitive_extra_is_redacted(caplog_records):
    """extra={"transcript": <object>} leaks through its repr just the same."""
    logger, handler = caplog_records
    logger.info("turn_end", extra={"transcript": [UTTERANCE, "second turn"]})
    out = _egress(handler.records[0])
    assert UTTERANCE not in out
    assert "diabetes" not in out
    assert "chars=" in out


def test_non_string_sensitive_argument_is_redacted(caplog_records):
    """`transcript=%r` handed an object still leaks via its repr."""
    logger, handler = caplog_records
    logger.info("turn_end transcript=%r turn_id=%d", [UTTERANCE], 4)
    out = handler.records[0].getMessage()
    assert UTTERANCE not in out
    assert "diabetes" not in out
    assert "turn_id=4" in out  # numeric slots untouched


# ---------------------------------------------------------------------------
# Install mechanics
# ---------------------------------------------------------------------------

def test_install_is_idempotent_and_reversible():
    install_pii_log_redaction()
    assert install_pii_log_redaction() is False  # already installed, no double wrap
    try:
        assert uninstall_pii_log_redaction() is True
        assert uninstall_pii_log_redaction() is False
    finally:
        install_pii_log_redaction()
    assert getattr(logging.Logger.makeRecord, "_pii_redacting", False) is True


def test_voice_pipeline_import_installs_the_redactor():
    """The pipeline modules must not depend on anyone else wiring this up."""
    uninstall_pii_log_redaction()
    import importlib

    importlib.reload(importlib.import_module("app.domain.services.voice_pipeline.transcript_handler"))
    assert getattr(logging.Logger.makeRecord, "_pii_redacting", False) is True
