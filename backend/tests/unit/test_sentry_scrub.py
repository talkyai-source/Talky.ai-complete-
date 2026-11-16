"""Error reports must never carry customer data (readiness plan).

Behaviour tests for app.core.sentry_scrub plus the wiring in sentry_init.
The scrubber is a pure module, so nothing here needs the Sentry SDK except
the optional real round trip at the bottom.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.core.sentry_scrub import scrub_breadcrumb, scrub_event, scrub_transaction


def _msg(text: str) -> str:
    out = scrub_event({"event_id": "e1", "message": text})
    assert out is not None
    return out["message"]


# --------------------------------------------------------------------------
# Patterns inside strings
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, leaked, marker",
    [
        ("call to +442071234567 failed", "442071234567", "[phone]"),
        ("dialing 020 7123 4567 now", "7123 4567", "[phone]"),
        ("dialing 07911 123456 now", "07911 123456", "[phone]"),
        ("dialing (415) 555-2671 now", "555-2671", "[phone]"),
        ("number +44 20 7123 4567 busy", "7123 4567", "[phone]"),
        ("card 4111 1111 1111 1111 declined", "4111 1111", "[phone]"),
        ("lead jane.doe+x@example.co.uk replied", "jane.doe", "[email]"),
        ("Authorization: Bearer abc.DEF-123_xyz=", "abc.DEF", "[token]"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.SflKxwRJSMeKKF2QT4fw ok",
            "eyJhbGci",
            "[token]",
        ),
        ("key sk-proj-abcdefghijklmnop1234 used", "abcdefghijklmnop", "[key]"),
        ("key sk_live_51HabcdefGHIJKLmnop used", "51Habcdef", "[key]"),
        ("hook whsec_abcdefghijklmnopqrstuv bad", "abcdefghijklmnop", "[key]"),
        ("hook ghp_abcdefghijklmnopqrstuvwxyz0123 bad", "abcdefghij", "[key]"),
        ("aws AKIAIOSFODNN7EXAMPLE leaked", "IOSFODNN7", "[key]"),
        ("slack xoxb-1234567890-abcdefghijkl leaked", "1234567890-abc", "[key]"),
    ],
)
def test_pattern_scrubbed(text, leaked, marker):
    out = _msg(text)
    assert leaked not in out
    assert marker in out


def test_url_credentials_keep_scheme_and_host():
    out = _msg("db down postgresql://app:S3cretPw@db.internal:5432/talky")
    assert "S3cretPw" not in out
    assert "app:" not in out
    assert "postgresql://[redacted]@db.internal:5432/talky" in out


def test_url_query_keeps_keys_scrubs_values():
    out = _msg("GET /cb?token=abc123&page=2&email=a@b.com&name=bob")
    assert "token=[redacted]" in out
    assert "abc123" not in out
    assert "page=2" in out
    assert "a@b.com" not in out
    assert "name=bob" in out


@pytest.mark.parametrize(
    "text",
    [
        "stt failed text='my card number is 4111' to=x",
        'stt failed text="it\'s me, call 5551234567" to=x',
        "got transcript='hello there' ok",
        "utterance='hi' ok",
        "said='something private' ok",
        "user_text='private words' ok",
        "reply='private words' ok",
    ],
)
def test_quoted_free_speech_after_marker_redacted(text):
    out = _msg(text)
    for secret in ("card number", "call 5551234567", "hello there", "'hi'", "something private", "private words"):
        assert secret not in out
    assert "=[redacted]" in out


def test_text_marker_exact_shape():
    assert _msg("stt failed text='abc def' to=x") == "stt failed text=[redacted] to=x"


@pytest.mark.parametrize(
    "text",
    [
        "call 123e4567-e89b-12d3-a456-426614174000 ended",
        "call 12345678-1234-1234-1234-123456789012 ended",
        "at 2026-11-16T10:20:30.123456Z done",
        "at 2026-11-16 10:20:30 done",
        "commit 3f786850e387550fdab836ed7e6dc881de23001b built",
        "status=404 for path",
        "attempt 3 of 5",
        "line 1234 in module",
        "HTTP 503 returned",
        "version 1.2.3 ok",
        "peer 192.168.100.200 connected",
        "An ordinary sentence with nothing private in it.",
        "env context=production ok",
        "go next=page2 ok",
        "subtext=abc def stays",
    ],
)
def test_ordinary_values_survive(text):
    assert _msg(text) == text


# --------------------------------------------------------------------------
# Key-name redaction
# --------------------------------------------------------------------------
def test_key_names_redacted_at_depth_and_case_insensitive():
    event = {
        "event_id": "e1",
        "extra": {
            "a": {"b": {"c": {"API_KEY": "plain-secret-1", "x-api-key": "plain-secret-2"}}},
            "Authorization": "Basic zzz",
            "user_password": "pw",
            "transcript": "hello i am bob",
            "prompt": "you are an agent",
            "messages": [{"role": "user", "content": "hi"}],
            "recording_url": "https://s3/x.mp3",
            "session_id": "abc",
            "otp": "123456",
            "keep": "fine",
            "max_tokens": 256,
            "content_type": "application/json",
        },
    }
    out = scrub_event(event)
    blob = json.dumps(out)
    for s in ("plain-secret-1", "plain-secret-2", "Basic zzz", "hello i am bob", "you are an agent", "s3/x.mp3", "123456"):
        assert s not in blob
    assert out["extra"]["a"]["b"]["c"]["API_KEY"] == "[redacted]"
    assert out["extra"]["messages"] == "[redacted]"
    assert out["extra"]["keep"] == "fine"
    assert out["extra"]["max_tokens"] == 256
    assert out["extra"]["content_type"] == "application/json"


def test_structure_keys_survive():
    event = {
        "event_id": "e1",
        "timestamp": "2026-11-16T10:00:00Z",
        "level": "error",
        "logger": "app.x",
        "release": "abc123def456",
        "environment": "production",
        "transaction": "/api/v1/calls",
        "fingerprint": ["a", "b"],
        "message": "boom",
        "exception": {"values": [{"type": "ValueError", "value": "bad", "stacktrace": {"frames": [
            {"filename": "a.py", "function": "f", "lineno": 12, "in_app": True}]}}]},
    }
    out = scrub_event(event)
    for k in ("event_id", "timestamp", "level", "logger", "release", "environment", "transaction", "fingerprint", "message"):
        assert out[k] == event[k]
    v = out["exception"]["values"][0]
    assert v["type"] == "ValueError" and v["value"] == "bad"
    assert v["stacktrace"]["frames"][0]["lineno"] == 12


def test_stack_frame_vars_scrubbed():
    event = {"event_id": "e1", "exception": {"values": [{"type": "E", "value": "x", "stacktrace": {"frames": [
        {"function": "f", "vars": {
            "phone": "+442071234567",
            "api_key": "whatever",
            "note": "mail me at a@b.com",
            "n": 3,
        }}]}}]}}
    frame = scrub_event(event)["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["vars"]["phone"] == "[phone]"
    assert frame["vars"]["api_key"] == "[redacted]"
    assert "a@b.com" not in frame["vars"]["note"]
    assert frame["vars"]["n"] == 3


def test_exception_value_logentry_and_params():
    event = {
        "event_id": "e1",
        "logentry": {"message": "to=%s key=%s", "params": ["+442071234567", "sk-abcdefghijklmnop12345"]},
        "exception": {"values": [{"type": "E", "value": "bad number +14155552671 for a@b.com"}]},
    }
    out = scrub_event(event)
    blob = json.dumps(out)
    assert "4155552671" not in blob and "a@b.com" not in blob and "abcdefghijklmnop" not in blob
    assert out["logentry"]["params"] == ["[phone]", "[key]"]


# --------------------------------------------------------------------------
# Request / user / breadcrumbs
# --------------------------------------------------------------------------
def test_request_rules():
    event = {"event_id": "e1", "request": {
        "url": "https://user:pw@api.example.com/x?token=abc&page=2",
        "query_string": "token=abc&page=2&email=a@b.com",
        "method": "POST",
        "headers": {
            "User-Agent": "ua", "Content-Type": "application/json", "Accept": "*/*",
            "X-Request-Id": "rid-1", "Authorization": "Bearer abc", "Cookie": "s=1",
            "X-Forwarded-For": "1.2.3.4",
        },
        "cookies": {"s": "1"},
        "data": {"transcript": "hello"},
    }}
    req = scrub_event(event)["request"]
    assert "cookies" not in req and "data" not in req
    assert set(k.lower() for k in req["headers"]) == {"user-agent", "content-type", "accept", "x-request-id"}
    assert "pw@" not in req["url"] and "token=abc" not in req["url"] and "page=2" in req["url"]
    assert "api.example.com" in req["url"]
    assert "abc" not in req["query_string"] and "a@b.com" not in req["query_string"]
    assert "page=2" in req["query_string"]
    assert req["method"] == "POST"


def test_user_reduced_to_id():
    out = scrub_event({"event_id": "e1", "user": {"id": "u-1", "email": "a@b.com", "ip_address": "1.2.3.4", "username": "bob"}})
    assert out["user"] == {"id": "u-1"}


def test_breadcrumbs_in_event_and_standalone():
    event = {"event_id": "e1", "breadcrumbs": {"values": [
        {"category": "log", "level": "warning", "message": "dial +442071234567", "data": {"password": "x", "url": "https://h/p?token=t&a=1"}},
    ]}}
    crumb = scrub_event(event)["breadcrumbs"]["values"][0]
    assert crumb["category"] == "log" and crumb["level"] == "warning"
    assert "[phone]" in crumb["message"]
    assert crumb["data"]["password"] == "[redacted]"
    assert "token=[redacted]" in crumb["data"]["url"] and "a=1" in crumb["data"]["url"]

    c2 = scrub_breadcrumb({"category": "x", "message": "mail a@b.com", "data": {"secret": "s"}})
    assert c2["message"] == "mail [email]" and c2["data"]["secret"] == "[redacted]"


def test_breadcrumb_that_cannot_be_scrubbed_is_dropped():
    class Bad(dict):
        def items(self):
            raise RuntimeError("boom")

    assert scrub_breadcrumb(Bad(message="a@b.com")) is None
    assert scrub_breadcrumb("not a dict") is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Bounds and failure
# --------------------------------------------------------------------------
def test_depth_and_size_caps():
    deep: dict = {}
    cur = deep
    for _ in range(40):
        cur["n"] = {}
        cur = cur["n"]
    cur["leak"] = "a@b.com"
    out = scrub_event({"event_id": "e1", "extra": {"deep": deep, "many": list(range(1000)), "long": "x" * 10000 + " a@b.com"}})
    assert "a@b.com" not in json.dumps(out)
    assert len(out["extra"]["many"]) <= 200
    assert len(out["extra"]["long"]) <= 2100


def test_string_cut_before_matching_cannot_leak_tail_marker():
    out = _msg("x" * 1990 + " text='private words here and more private words")
    assert "private" not in out


def test_raising_event_returns_minimal_event_never_original():
    class Bad(dict):
        def items(self):
            raise RuntimeError("boom")

    original = Bad(
        event_id="e1", timestamp="2026-11-16T10:00:00Z", level="error", release="r1", environment="prod",
        message="secret +442071234567",
        exception={"values": [{"type": "ValueError", "value": "a@b.com"}]},
        extra={"password": "p"},
    )
    out = scrub_event(original)
    assert out is not original
    assert out == {
        "event_id": "e1", "timestamp": "2026-11-16T10:00:00Z", "level": "error", "release": "r1",
        "environment": "prod", "exception": {"values": [{"type": "ValueError"}]},
        "message": "event withheld: scrubbing failed",
    }


def test_non_dict_event_never_returned_as_is():
    out = scrub_event("a@b.com")  # type: ignore[arg-type]
    assert out is None or out.get("message") == "event withheld: scrubbing failed"


def test_input_not_mutated():
    event = {"event_id": "e1", "message": "a@b.com", "user": {"id": "1", "email": "a@b.com"}}
    scrub_event(event)
    assert event["message"] == "a@b.com" and "email" in event["user"]


# --------------------------------------------------------------------------
# Unquoted speech, logentry placeholder walk, transactions
# --------------------------------------------------------------------------
def test_unquoted_value_after_marker_redacted_up_to_next_key():
    assert _msg("stt failed text=hello there to=+442071234567") == "stt failed text=[redacted] to=[phone]"
    assert _msg("reply=ok my number is 07911 123456") == "reply=[redacted]"


def _le(template, params, **extra):
    out = scrub_event({"event_id": "e1", "logentry": {"message": template, "params": params, **extra}})
    return out["logentry"]


def test_logentry_positional_marker_argument_redacted():
    le = _le("stt failed text=%r to=%s", ["my card number is 4111", "+442071234567"])
    assert le["message"] == "stt failed text=%r to=%s"
    assert le["params"] == ["[redacted]", "[phone]"]


def test_logentry_named_marker_argument_redacted():
    le = _le("x text=%(text)s to=%(to)s", {"text": "hello", "to": "+442071234567"})
    assert le["params"] == {"text": "[redacted]", "to": "[phone]"}


def test_logentry_percent_percent_is_skipped():
    assert _le("100%% done text=%s", ["secret words"])["params"] == ["[redacted]"]


def test_logentry_width_and_precision():
    assert _le("a=%5.2f text=%-10s", [3.14159, "hello"])["params"] == [3.14159, "[redacted]"]


def test_logentry_quoted_placeholder_after_marker():
    assert _le("text='%s' n=%d", ["hello", 3])["params"] == ["[redacted]", 3]


def test_logentry_count_mismatch_redacts_all_strings():
    assert _le("text=%s to=%s", ["only one secret"])["params"] == ["[redacted]"]
    assert _le("x %s", ["a b", 5])["params"] == ["[redacted]", 5]
    assert _le("x %s", {"k": "v w"})["params"] == {"k": "[redacted]"}
    assert _le("x %(k)s", ["v w"])["params"] == ["[redacted]"]
    assert _le("x %*d %s", [3, "v w"])["params"] == [3, "[redacted]"]


def test_logentry_non_string_under_marker_redacted_whatever_the_type():
    assert _le("text=%s", [12345678901])["params"] == ["[redacted]"]
    assert _le("text=%s", [{"a": "b"}])["params"] == ["[redacted]"]


def test_logentry_without_marker_only_pattern_scrubbed():
    assert _le("to=%s mail=%s", ["+442071234567", "a@b.com"])["params"] == ["[phone]", "[email]"]


def test_logentry_formatted_goes_through_string_rules():
    le = _le("text=%s to=%s", ["hi there", "+442071234567"], formatted="text=hi there to=+442071234567")
    assert le["formatted"] == "text=[redacted] to=[phone]"


def test_logentry_tuple_params_and_garbage_never_raise():
    assert _le("text=%s", ("hello",))["params"] == ["[redacted]"]
    out = scrub_event({"event_id": "e1", "logentry": {"message": None, "params": 5}})
    assert out is not None


def test_transaction_that_cannot_be_scrubbed_is_dropped():
    class Bad(dict):
        def items(self):
            raise RuntimeError("boom")

    assert scrub_transaction(Bad(type="transaction")) is None
    assert scrub_transaction("nope") is None  # type: ignore[arg-type]
    ok = scrub_transaction({"type": "transaction", "transaction": "/x", "request": {"url": "/a?token=t"}})
    assert ok["type"] == "transaction" and ok["request"]["url"] == "/a?token=[redacted]"


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------
def test_init_wires_scrub_hooks_and_pii_off(monkeypatch: pytest.MonkeyPatch):
    import app.core.sentry_init as mod

    monkeypatch.setenv("SENTRY_DSN", "https://test@o0.ingest.sentry.io/0")
    fake_init = MagicMock()
    fake_modules = {
        "sentry_sdk": MagicMock(init=fake_init),
        "sentry_sdk.integrations.fastapi": MagicMock(FastApiIntegration=MagicMock),
        "sentry_sdk.integrations.starlette": MagicMock(StarletteIntegration=MagicMock),
        "sentry_sdk.integrations.asyncio": MagicMock(AsyncioIntegration=MagicMock),
        "sentry_sdk.integrations.logging": MagicMock(LoggingIntegration=MagicMock),
    }
    with patch.dict("sys.modules", fake_modules):
        assert mod.init_sentry() is True
    kwargs = fake_init.call_args.kwargs
    assert kwargs["before_send"] is scrub_event
    assert kwargs["before_send_transaction"] is scrub_transaction
    assert kwargs["before_breadcrumb"] is scrub_breadcrumb
    assert kwargs["send_default_pii"] is False
    # Speech held in an ordinary local variable has no marker a pattern can
    # find, so stack-frame variables are not sent at all.
    assert kwargs["include_local_variables"] is False


# --------------------------------------------------------------------------
# Real SDK round trip (skipped where sentry_sdk is not installed)
# --------------------------------------------------------------------------
# Built at run time so the words never sit in a source line the SDK attaches.
_SPEECH = " ".join(["my", "card", "number", "is", "4111", "1111", "1111", "1111"])


def test_real_sdk_round_trip_sends_nothing_sensitive():
    sentry_sdk = pytest.importorskip("sentry_sdk")
    from sentry_sdk.integrations.logging import LoggingIntegration

    sent: list = []
    sentry_sdk.init(
        dsn="https://public@o0.ingest.sentry.io/0",
        transport=sent.append,
        before_send=scrub_event,
        before_breadcrumb=scrub_breadcrumb,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR)],
        send_default_pii=False,
        include_local_variables=False,  # as in production (sentry_init)
    )
    log = logging.getLogger("sentry_scrub_roundtrip")
    log.warning("warm up to=%s", "+442071234567")
    log.error("stt failed text=%r to=%s", _SPEECH, "+442071234567")
    log.error("stt failed text=%s to=%s", _SPEECH, "+442071234567")
    log.error("stt failed text=%(text)s", {"text": _SPEECH})

    def boom():
        phone = "+442071234567"
        api_key = "sk-abcdefghijklmnop12345678"  # noqa: F841
        heard = _SPEECH.upper()  # noqa: F841 - speech in a plain local, no marker
        raise ValueError(f"cannot dial {phone}")

    try:
        boom()
    except ValueError:
        sentry_sdk.capture_exception()
    sentry_sdk.flush()
    sentry_sdk.get_client().close()

    assert len(sent) >= 2
    blob = json.dumps(sent, default=str)
    for secret in ("4111", "442071234567", "abcdefghijklmnop", "card number", "my card number",
                   "CARD NUMBER"):
        assert secret not in blob
    # No stack-frame variables leave the process at all.
    frames = [
        frame
        for event in sent if isinstance(event, dict)
        for exc in (event.get("exception") or {}).get("values") or []
        for frame in (exc.get("stacktrace") or {}).get("frames") or []
    ]
    assert frames and all(not frame.get("vars") for frame in frames)
    entries = [e["logentry"] for e in sent if isinstance(e, dict) and e.get("logentry")]
    by_template = {le["message"]: le["params"] for le in entries}
    assert by_template["stt failed text=%r to=%s"][0] == "[redacted]"
    assert by_template["stt failed text=%s to=%s"][0] == "[redacted]"
    assert by_template["stt failed text=%(text)s"] == {"text": "[redacted]"}
