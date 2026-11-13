"""QA F2-10 follow-up: personal data inside errors and containers is masked too.

The log redactor masked text arguments, f-strings and ``extra`` fields, but an
exception object, a dict or a list passed as an argument, and the traceback of
``logger.exception`` reached the log untouched. Database errors quote the
offending value ("Key (phone)=(+447700900123) already exists"), so a phone
number or an email address could still be written in clear.
"""
from __future__ import annotations

import logging
import sys

import pytest

from app.core.log_redact import redact_log_record

PHONE = "+447700900123"
EMAIL = "john.smith@example.com"


def _render(msg, args=None, exc_info=None) -> str:
    record = logging.LogRecord("talky.test", logging.ERROR, __file__, 1, msg, args, exc_info)
    redact_log_record(record)
    return logging.Formatter("%(message)s").format(record)


def _exc_info(message: str):
    try:
        raise ValueError(message)
    except ValueError:
        return sys.exc_info()


@pytest.fixture(autouse=True)
def _redaction_on(monkeypatch):
    monkeypatch.delenv("TALKY_PII_LOG_REDACTION", raising=False)
    monkeypatch.delenv("PII_LOG_REDACTION", raising=False)


def test_an_exception_passed_as_an_argument_is_masked():
    out = _render("lead import failed: %s", (ValueError(f"duplicate key (phone)=({PHONE})"),))
    assert PHONE not in out
    assert "lead import failed: duplicate key (phone)=(" in out


def test_a_dict_and_a_list_passed_as_arguments_are_masked():
    out = _render("payload %s and %s", ({"phone": "07700 900123", "n": 1}, [EMAIL]))
    assert "900123" not in out
    assert "john.smith" not in out
    assert "'n': 1" in out


def test_a_container_in_mapping_style_arguments_is_masked():
    out = _render("contacts %(c)s", ({"c": [PHONE, EMAIL]},))
    assert PHONE not in out and "john.smith" not in out


def test_the_traceback_of_logger_exception_is_masked():
    out = _render("upsert failed", exc_info=_exc_info(f"Key (email)=({EMAIL}) already exists for {PHONE}"))
    assert "Traceback (most recent call last)" in out
    assert "ValueError: Key (email)=(" in out
    assert "john.smith" not in out and PHONE not in out


@pytest.mark.parametrize("args,expected", [
    ((ValueError("connection reset"),), "failed: connection reset"),
    (([1, 2, 3],), "failed: [1, 2, 3]"),
    (({"status": 503, "retry": True},), "failed: {'status': 503, 'retry': True}"),
])
def test_errors_and_containers_without_personal_data_are_unchanged(args, expected):
    assert _render("failed: %s", args) == expected


def test_a_traceback_without_personal_data_is_unchanged():
    exc_info = _exc_info("connection reset by peer")
    plain = logging.LogRecord("talky.test", logging.ERROR, __file__, 1, "boom", None, exc_info)
    expected = logging.Formatter("%(message)s").format(plain)
    assert _render("boom", exc_info=exc_info) == expected
