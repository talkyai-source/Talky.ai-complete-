"""The error reference a user is shown must be the one in the log (QA F2-07).

The voice sockets logged `ref=<uuid4 hex[:12]>`. When those 12 hex characters
held 11+ digits in a row (about 1 in 140 references), the log redactor's
`longrun` rule masked them as a phone/card number, so the logged reference no
longer matched the one on the page (CI flake on PR #43, 12 Nov 2026). The
reference is now letters only.
"""
import pytest

from app.core import log_redact


@pytest.fixture(autouse=True)
def redaction_on():
    log_redact.set_pii_redaction_enabled(True)
    yield
    log_redact.set_pii_redaction_enabled(None)


def test_an_all_digit_hex_reference_is_masked_in_the_log():
    # The old shape, at its unlucky end: the reason for this change.
    assert "123456789007" not in log_redact.scrub_text("Ask AI error ref=123456789007 session=demo-1")


def test_references_are_letters_only_and_survive_the_redactor():
    for _ in range(2000):
        reference = log_redact.error_reference()
        assert len(reference) == 12 and reference.isalpha() and reference.islower()
        line = f"Ask AI error ref={reference} session=demo-1: asyncpg failure"
        assert reference in log_redact.scrub_text(line)


def test_both_voice_sockets_use_it():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "endpoints"
    for name in ("ask_ai_ws.py", "campaign_test_ws.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "reference = error_reference()" in source and "uuid4().hex[:12]" not in source, name
