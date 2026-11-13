"""Credentials and UK national numbers must not reach the log (QA F2-10, QA-BE-110).

b38d3b1a made every worker install the PII redactor, but the patterns behind
it had no rule for credentials or for UK national-format numbers, so a Bearer
header, a JWT, a Stripe key, ``password=...`` or ``07700 900123`` still went
to the journal in clear.

Fixtures are assembled at runtime from fragments so the secret scanner that
runs in CI (gitleaks) does not flag this file. None of them is a credential.
"""
from __future__ import annotations

import logging
import re
import time

import pytest

from app.core import log_redact
from app.core.log_redact import REDACTED_PREFIX, scrub_text

MASK = "[redacted secret]"
TAIL = "q7Zk2mVx9TbLw4RpNc8HdYe5"  # opaque 24-character value
BODY = "4eC39HqLyjWDarjtT1zdp7dc"
JWT = ".".join([
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ",
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
])


def _masked_digits(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return f"[redacted digits={len(digits)} ..{digits[-2:]}]"


# ---------------------------------------------------------------------------
# Bearer tokens
# ---------------------------------------------------------------------------

def test_bearer_header_value_is_masked_and_the_scheme_stays_visible():
    line = "Authorization: Bearer " + TAIL + " from 10.0.0.5"
    assert scrub_text(line) == "Authorization: Bearer " + MASK + " from 10.0.0.5"


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "Bearer"])
def test_bearer_scheme_is_case_insensitive(scheme):
    out = scrub_text(f"got header {scheme} {TAIL} ok")
    assert TAIL not in out
    assert out == f"got header {scheme} {MASK} ok"


def test_bearer_token_with_url_safe_characters_is_masked_whole():
    opaque = "-".join(["abc", "DEF"]) + "_ghi.jkl~mno+pqr/stu"
    out = scrub_text(f"Authorization: Bearer {opaque}== end")
    assert "abc-DEF" not in out and "pqr/stu" not in out
    assert out.startswith("Authorization: Bearer " + MASK)


def test_an_authorization_header_with_another_scheme_loses_its_credential_too():
    out = scrub_text("authorization: Basic " + TAIL + " retry=1")
    assert TAIL not in out
    assert out == "authorization: Basic " + MASK + " retry=1"
    assert scrub_text(out) == out  # masking twice changes nothing


# ---------------------------------------------------------------------------
# JWTs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "template",
    [
        "got {jwt} from the client",
        "callback?x={jwt}&y=1",
        '{{"jwt": "{jwt}"}}',
        "({jwt})",
        "cookie={jwt}; Path=/",
        "{jwt}.",
    ],
)
def test_a_jwt_is_masked_wherever_it_appears(template):
    out = scrub_text(template.format(jwt=JWT))
    for segment in JWT.split("."):
        assert segment not in out
    assert MASK in out


def test_a_jwt_with_a_long_digit_segment_is_masked_as_a_jwt_not_nibbled_as_digits():
    jwt = "eyJhbGciOiJIUzI1NiJ9." + "12345678901234567890" + ".sig"
    out = scrub_text("token seen " + jwt + " done")
    assert out == "token seen " + MASK + " done"


def test_an_unsigned_jwt_with_an_empty_signature_is_masked():
    jwt = "eyJhbGciOiJub25lIn0." + "eyJzdWIiOiJ4In0" + "."
    assert scrub_text("alg none " + jwt + " end") == "alg none " + MASK + " end"


def test_text_that_merely_starts_with_eyj_is_not_a_jwt():
    for line in ("eyJhbGci alone", "name=eyJ.", "see eyJ.x only two segments"):
        assert scrub_text(line) == line


# ---------------------------------------------------------------------------
# Stripe-style secrets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["sk_live_", "sk_test_", "rk_live_", "rk_test_", "whsec_"])
def test_stripe_style_secrets_keep_their_prefix_and_lose_the_body(prefix):
    out = scrub_text(f"stripe rejected {prefix}{BODY} for tenant")
    assert BODY not in out
    assert out == f"stripe rejected {prefix}{MASK} for tenant"


def test_a_publishable_key_and_prose_about_keys_are_left_alone():
    line = "pk_live_" + BODY + " is public; the sk_live_ prefix marks a secret key"
    out = scrub_text(line)
    assert "pk_live_" + BODY in out  # publishable keys are not secret


# ---------------------------------------------------------------------------
# key=value, key: value and JSON "key": "value"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "line, expected",
    [
        ("password=hunter2", "password=" + MASK),
        ("PASSWORD=hunter2", "PASSWORD=" + MASK),
        ("passwd=hunter2", "passwd=" + MASK),
        ("secret=s3cr3t", "secret=" + MASK),
        ("token=abc123", "token=" + MASK),
        ("api_key=abc123", "api_key=" + MASK),
        ("apikey=abc123", "apikey=" + MASK),
        ("Api-Key: abc123", "Api-Key: " + MASK),
        ("authorization=abc123", "authorization=" + MASK),
        ("password = hunter2", "password = " + MASK),
        ("password: hunter2", "password: " + MASK),
        ("password : hunter2", "password : " + MASK),
        ("Token: abc123", "Token: " + MASK),
        ('{"password": "hunter2"}', '{"password": "' + MASK + '"}'),
        ('{"password":"hunter2"}', '{"password":"' + MASK + '"}'),
        ("{'password': 'hunter2'}", "{'password': '" + MASK + "'}"),
        ('{"api_key": "abc123", "n": 1}', '{"api_key": "' + MASK + '", "n": 1}'),
        ('password="two words here" next', 'password="' + MASK + '" next'),
        ("password='it\\'s' next", "password='" + MASK + "' next"),
        ('{"password": 12345, "x": 1}', '{"password": ' + MASK + ', "x": 1}'),
        # compound names that END in a secret word
        ("DB_PASSWORD=hunter2", "DB_PASSWORD=" + MASK),
        ("access_token=abc123", "access_token=" + MASK),
        ("client_secret=abc123", "client_secret=" + MASK),
        ("JWT_SECRET_KEY=abc123", "JWT_SECRET_KEY=" + MASK),
        ("X-API-Key: abc123", "X-API-Key: " + MASK),
        ('{"refresh_token": "abc123"}', '{"refresh_token": "' + MASK + '"}'),
        # surrounding structure survives
        ("login failed password=hunter2 user=bob", "login failed password=" + MASK + " user=bob"),
        ("/cb?token=abc123&state=xyz", "/cb?token=" + MASK + "&state=xyz"),
        ("(token=abc123)", "(token=" + MASK + ")"),
        ("token=abc123, user=bob", "token=" + MASK + ", user=bob"),
        ("cookie session_token=abc123; Path=/", "cookie session_token=" + MASK + "; Path=/"),
        ("password=hunter2.", "password=" + MASK + "."),
    ],
)
def test_secret_values_are_masked_in_every_key_value_form(line, expected):
    assert scrub_text(line) == expected


SECRET_NAMES = [
    "password", "passwd", "secret", "secret_key", "secretkey", "secret-key",
    "token", "api_key", "api-key", "apikey", "authorization",
]
KEY_VALUE_FORMS = [
    ("{n}=s3cr3t", "{n}={m}"),
    ("{n}:s3cr3t", "{n}:{m}"),
    ("{n}: s3cr3t", "{n}: {m}"),
    ("{n} = s3cr3t", "{n} = {m}"),
    ('"{n}": "s3cr3t"', '"{n}": "{m}"'),
    ("'{n}': 's3cr3t'", "'{n}': '{m}'"),
]


@pytest.mark.parametrize("name", SECRET_NAMES)
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
def test_every_secret_name_is_masked_in_every_form_and_case(name, case):
    # Ties the cheap trigger in front of the key/value rule to the rule itself:
    # a name or separator the rule accepts but the trigger misses would leak.
    n = case(name)
    for line, expected in KEY_VALUE_FORMS:
        assert scrub_text(line.format(n=n)) == expected.format(n=n, m=MASK), (n, line)


def test_a_secret_made_of_digits_is_masked_as_a_secret_not_as_a_card_number():
    out = scrub_text("password=4111111111111111 token=123456")
    assert out == "password=" + MASK + " token=" + MASK
    assert "redacted digits" not in out  # no ..11 fragment of the secret left behind


def test_a_secret_value_with_a_long_unbroken_run_is_masked_whole():
    value = "Zq" * 300
    out = scrub_text("secret=" + value + " ok")
    assert value[:20] not in out
    assert out == "secret=" + MASK + " ok"


def test_an_unterminated_quote_still_hides_the_value():
    out = scrub_text('{"password": "hunt')
    assert "hunt" not in out
    assert out.startswith('{"password": "' + MASK)


@pytest.mark.parametrize(
    "line",
    [
        "api_key=None retries=3",
        "token=null",
        "token=false",
        "password=",
        'password=""',
        '{"token": null}',
        "password=[redacted secret]",
        "token=[redacted chars=5 sha=1f3a9c2d]",
        # names that only CONTAIN a secret word are not secret fields
        "max_tokens=500 prompt_tokens=12 completion_tokens=34 total_tokens=46",
        "token_count=5 secret_count=2 token_type=Bearer",
        "passwords_checked=3 password_length=12 apikey_configured=true",
        "authorization_url=https://auth.example.com/x",
        '{"token_type": "Bearer", "expires_in": 3600}',
        "Bearer",
        "the bearer",
        # nothing but punctuation after the key: there is no value to hide
        "token=, retry in 5s",
        "password=).",
    ],
)
def test_lines_that_only_mention_secret_words_are_unchanged(line):
    assert scrub_text(line) == line


def test_already_masked_text_is_not_masked_again():
    line = " ".join([
        "Authorization: Bearer " + TAIL,
        "password=hunter2",
        '{"token": "abc123"}',
        "sk_live_" + BODY,
        JWT,
        "call 07700 900123",
        "mail john@example.com",
        "dial +14155552671",
    ])
    once = scrub_text(line)
    assert scrub_text(once) == once
    assert once.count(MASK) >= 5


def test_the_secret_marker_is_a_redaction_marker():
    assert log_redact.REDACTED_SECRET == MASK
    assert MASK.startswith(REDACTED_PREFIX)  # so "already redacted" checks see it


# ---------------------------------------------------------------------------
# UK national-format numbers
# ---------------------------------------------------------------------------

UK_NUMBERS = [
    "07700 900123",
    "07700900123",
    "07700 900 123",
    "07700-900123",
    "020 7946 0958",
    "02079460958",
    "020-7946-0958",
    "020 79460958",
    "(020) 7946 0958",
    "0161 496 0000",
    "01614960000",
    "0161 4960000",
    "01632 960983",
    "01632960983",
    "(01632) 960983",
    "0800 123456",
    "0800 123 4567",
    "0300 123 4567",
]


@pytest.mark.parametrize("number", UK_NUMBERS)
def test_uk_national_numbers_are_masked_with_or_without_spaces(number):
    out = scrub_text(f"callback requested on {number} by the lead")
    assert out == f"callback requested on {_masked_digits(number)} by the lead"


@pytest.mark.parametrize(
    "template",
    [
        "call me on {n}.",
        "numbers: {n}, then more",
        '{{"phone": "{n}"}}',
        "tel:{n}",
        "[{n}]",
        "to={n}",
    ],
)
def test_uk_numbers_are_masked_in_every_context(template):
    out = scrub_text(template.format(n="020 7946 0958"))
    assert "7946" not in out
    assert "redacted digits=11 ..58" in out


def test_a_sentence_ending_dot_does_not_hide_a_uk_number():
    assert scrub_text("call me on 07700 900123.") == "call me on " + _masked_digits("07700900123") + "."


def test_brackets_round_a_whole_number_are_left_balanced():
    assert scrub_text("(07700 900123) rang") == "(" + _masked_digits("07700900123") + ") rang"
    assert scrub_text("x ((0161) 496 0000)") == "x (" + _masked_digits("01614960000") + ")"


@pytest.mark.parametrize("line", ["1.07700 900123", "507700 900123"])
def test_a_uk_number_glued_to_a_digit_or_a_decimal_point_is_not_a_uk_number(line):
    assert "[redacted digits=11" not in scrub_text(line)


@pytest.mark.parametrize(
    "line",
    [
        "started 2026-11-12 09:33:11 took 0.52s port=5060 status=200 duration=12.5s count=42",
        "05-12-2026 09:33 deploy",
        "epoch=1731403991 seq=0007 sku 0042",
        "chunks 0001 0002 of 0456",
        "v3.12.12 build 0.1.2 on 127.0.0.1:5060",
        "elapsed 00:01:23 latency_ms=812.5 ratio 0.123456789",
        "offset 1.07700900123 is a decimal fraction",
        "retry in 0161 ms and 496 ms and 0000 ms",
    ],
)
def test_ordinary_numbers_are_not_mistaken_for_phone_numbers(line):
    assert scrub_text(line) == line


def test_a_uk_shaped_stretch_inside_a_uuid_stays_readable():
    line = "request_id=aaaaaaaa-bbbb-4ccc-0123-456789abcdef failed"
    assert scrub_text(line) == line


def test_a_hash_field_is_not_treated_as_a_uk_number():
    for line in ("signature=0161 496 0000 ok", "head=02079460958", "digest: 07700 900123"):
        assert scrub_text(line) == line
    assert "496" not in scrub_text("phone=0161 496 0000 ok")


def test_existing_masking_is_kept():
    out = scrub_text("a john.smith1990@example.com b +44 7911 123456 c 4111111111111111 d 078-05-1120")
    assert "john.smith1990" not in out and "@example.com" in out
    assert "7911" not in out and "4111111111111111" not in out and "078-05-1120" not in out
    uuid_line = "request_id=7a08427a-4d5f-4aa2-b54f-437141468111 ok"
    assert scrub_text(uuid_line) == uuid_line


@pytest.mark.parametrize(
    "line, shown",
    [
        ("x/john@example.com", "x/***@example.com"),
        ("to=+tag@example.com.", "to=***@example.com."),
        ("_a@sub.example.co.uk,", "***@sub.example.co.uk,"),
        ("a.b-c%d@example.com", "***@example.com"),
        ("n=12345 7@example.com", "n=12345 ***@example.com"),
    ],
)
def test_emails_are_still_masked_wherever_they_start(line, shown):
    assert scrub_text(line) == shown


# ---------------------------------------------------------------------------
# Through the real logging pipeline
# ---------------------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture()
def secret_log():
    log_redact.install_pii_log_redaction()
    log_redact.set_pii_redaction_enabled(True)
    logger = logging.getLogger("tests.secret_redaction")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Capture()
    logger.addHandler(handler)
    try:
        yield logger, handler
    finally:
        logger.removeHandler(handler)
        log_redact.set_pii_redaction_enabled(None)


def test_a_formatted_message_carrying_credentials_is_masked_in_the_record(secret_log):
    logger, handler = secret_log
    logger.warning(f"upstream said no: Authorization: Bearer {TAIL} password=hunter2 jwt={JWT}")
    message = handler.records[0].getMessage()
    assert TAIL not in message and "hunter2" not in message and "eyJhbGci" not in message
    assert message.startswith("upstream said no: Authorization: Bearer " + MASK)


def test_string_arguments_and_extras_are_masked(secret_log):
    logger, handler = secret_log
    logger.info("header=%s", "Authorization: Bearer " + TAIL)
    logger.info("stored", extra={"note": "api_key=abc123 for 020 7946 0958"})
    logger.info("%(blob)s", {"blob": '{"password": "hunter2"}'})
    first, second, third = handler.records
    assert TAIL not in first.getMessage() and MASK in first.getMessage()
    assert "abc123" not in second.note and "7946" not in second.note
    assert "hunter2" not in third.getMessage()


def test_a_worker_style_line_hides_a_uk_lead_number(secret_log):
    logger, handler = secret_log
    logger.info("Dialing lead %s for campaign %s", "0161 496 0000", "7f2c")
    assert handler.records[0].getMessage() == "Dialing lead [redacted digits=11 ..00] for campaign 7f2c"


def test_the_filter_form_on_top_of_the_hook_changes_nothing_more(secret_log):
    logger, handler = secret_log
    message = f"Authorization: Bearer {TAIL} password=hunter2 rang 07700 900123 {JWT}"
    handler.addFilter(log_redact.PIIRedactionFilter())
    logger.info(message)
    assert handler.records[0].getMessage() == scrub_text(message)
    assert "hunter2" not in handler.records[0].getMessage()


def test_ordinary_log_lines_pass_through_the_pipeline_untouched(secret_log):
    logger, handler = secret_log
    line = "turn_complete call=abc123def456 turn_id=4 latency_ms=812.5 tokens=%s"
    logger.info(line, 42)
    assert handler.records[0].getMessage() == line % 42


# ---------------------------------------------------------------------------
# Speed: no catastrophic backtracking on a 100 KB line
# ---------------------------------------------------------------------------

_HUNDRED_KB = 100 * 1024
_MIXED = (
    "Authorization: Bearer " + TAIL + " password=hunter2 rang 07700 900123 "
    "john@example.com +14155552671 4111111111111111 sk_live_" + BODY + " " + JWT + " "
)

SHAPES = {
    "realistic mixed line": _MIXED * (_HUNDRED_KB // len(_MIXED) + 1),
    "one unbroken digit run": "0" * _HUNDRED_KB,
    "one unbroken alphanumeric run": ("A12b34C56d78" * (_HUNDRED_KB // 12 + 1)),
    "base64url blob": ("QUJD12-_RGhp34amts9q0Zz" * (_HUNDRED_KB // 23 + 1)),
    "digits and spaces": "0 " * (_HUNDRED_KB // 2),
    # many matches on one line: each match must cost the same, not a rescan of the line so far
    "uk numbers back to back": "0161 496 0000 " * (_HUNDRED_KB // 14 + 1),
    "hyphenated uk numbers back to back": "020-7946-0958 " * (_HUNDRED_KB // 14 + 1),
    "phone pairs back to back": "phone=0161 496 0000 " * (_HUNDRED_KB // 20 + 1),
    "cards back to back": "4111 1111 1111 1111 " * (_HUNDRED_KB // 20 + 1),
    "bare 11-digit numbers back to back": "07700900123 " * (_HUNDRED_KB // 12 + 1),
    "e164 numbers back to back": "+14155552671 " * (_HUNDRED_KB // 13 + 1),
    "uk-shaped bare runs": "07700900123" * (_HUNDRED_KB // 11 + 1),
    "emails back to back": "a@b.co " * (_HUNDRED_KB // 7 + 1),
    "plus signs": "+44 " * (_HUNDRED_KB // 4),
    "key with a huge value": "token=" + "A" * _HUNDRED_KB,
    "key repeated": "password=a " * (_HUNDRED_KB // 11 + 1),
    "unterminated quotes": 'password="' * (_HUNDRED_KB // 10 + 1),
    "quote then trailing backslash": 'password="' + "x" * _HUNDRED_KB + "\\",
    "bearer repeated": "Bearer " * (_HUNDRED_KB // 7 + 1),
    "jwt prefixes without dots": "eyJ" * (_HUNDRED_KB // 3 + 1),
    "jwt prefixes with dots": "eyJ." * (_HUNDRED_KB // 4 + 1),
    "stripe prefixes": "sk_live_" * (_HUNDRED_KB // 8 + 1),
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_a_100kb_line_is_redacted_in_well_under_half_a_second(name):
    text = SHAPES[name]
    assert len(text) >= _HUNDRED_KB
    started = time.perf_counter()
    out = scrub_text(text)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"{name}: {elapsed:.2f}s"
    assert isinstance(out, str)


def test_the_speed_is_not_bought_by_skipping_the_work():
    out = scrub_text(SHAPES["realistic mixed line"])
    private = ("hunter2", TAIL, BODY, "07700 900123", "john@example.com", "4111111111111111", "eyJhbGci")
    # A list, not `assert s not in out`: on failure pytest would diff two 100 KB strings.
    assert [s for s in private if s in out] == []


@pytest.mark.parametrize(
    "line,secret",
    [
        # values that start like a container or like an "unset" word are still values
        ("password=[hunter2]", "hunter2"),
        ("token={abc123xyz}", "abc123xyz"),
        ("password=null-pointer-xyz", "pointer-xyz"),
        ("secret: true_story_123", "true_story_123"),
        ("api_key=None2026secret", "None2026secret"),
        # a Bearer token is hidden whatever it contains, "=" included
        ("Bearer abc=def123 next", "abc=def123"),
        ("Authorization: Bearer abc=def123", "abc=def123"),
        ("sent Bearer dG9rZW4=xyz and more", "dG9rZW4=xyz"),
    ],
)
def test_secrets_shaped_like_containers_or_keywords_are_still_hidden(line, secret):
    out = scrub_text(line)
    assert secret not in out
    assert MASK in out


def test_bearer_errs_toward_hiding_the_next_word():
    assert scrub_text("token_type=Bearer expires_in=3600") == "token_type=Bearer " + MASK
    assert scrub_text('WWW-Authenticate: Bearer realm="api", error="invalid_token"') == (
        'WWW-Authenticate: Bearer ' + MASK + '"api", error="invalid_token"'
    )
