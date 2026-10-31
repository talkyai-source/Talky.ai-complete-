"""Pure support pieces: attachment checks, signed links, projections, cursors,
receipts JSON, page context, metric labels and the worker schedule."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.domain.services.support import attachment_files as files
from app.domain.services.support import conversation_service, projection, receipts
from app.domain.services.support.errors import SupportValidationError
from app.domain.services.support.pagination import decode_cursor, encode_cursor, next_cursor
from app.infrastructure.metrics import support_metrics
from app.workers.support_jobs import should_run_hourly

MB = 1024 * 1024
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("name,data,content_type,filename", [
    ("shot.PNG", b"\x89PNG\r\n\x1a\n" + b"\0" * 8, "image/png", "shot.png"),
    ("photo.jpeg", b"\xff\xd8\xff\xe0" + b"\0" * 8, "image/jpeg", "photo.jpg"),
    ("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp", "a.webp"),
    ("invoice.pdf", b"%PDF-1.7\n", "application/pdf", "invoice.pdf"),
    ("server.log", b"2026-10-30 error x\n", "text/plain", "server.log"),
    ("leads.csv", b"name,phone\nA,+15550100\n", "text/csv", "leads.csv"),
])
def test_types_come_from_content(name, data, content_type, filename):
    checked = files.check_file(name, data, max_bytes=MB)
    assert (checked.content_type, checked.filename) == (content_type, filename)


@pytest.mark.parametrize("name,data", [
    ("evil.png", b"<html><script>alert(1)</script>"),   # text pretending to be an image
    ("binary.txt", b"\x00\x01\x02\x03"),                # binary pretending to be text
    ("doc.pdf", b"\x89PNG\r\n\x1a\n"),                  # png renamed .pdf
    ("page.html", b"<html></html>"),                    # unsupported extension
    ("archive.zip", b"PK\x03\x04"),
    ("noext", b"plain words"),
])
def test_mismatched_or_unsupported_files_are_refused(name, data):
    with pytest.raises(files.UnsupportedAttachment):
        files.check_file(name, data, max_bytes=MB)


def test_size_limits():
    with pytest.raises(SupportValidationError, match="at most 1 MB"):
        files.check_file("big.txt", b"a" * (MB + 1), max_bytes=MB)
    with pytest.raises(SupportValidationError):
        files.check_file("empty.txt", b"", max_bytes=MB)


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.txt", "passwd.txt"),
    ("C:\\Users\\me\\report final.txt", "report final.txt"),
    ("<script>.txt", "script.txt"),
    ("  ...  .txt", "attachment.txt"),
    ("résumé.txt", "r_sum.txt"),
])
def test_filenames_are_cleaned(raw, expected):
    assert files.clean_filename(raw, ".txt") == expected


def test_signed_links_expire_and_bind_the_attachment(monkeypatch):
    monkeypatch.setattr(files, "_signing_key", lambda: b"k" * 32)
    attachment = str(uuid4())
    expires, signature = files.sign_download(attachment, now=1000.0)
    assert files.verify_download(attachment, expires, signature, now=1000.0)
    assert not files.verify_download(str(uuid4()), expires, signature, now=1000.0)
    assert not files.verify_download(attachment, expires, signature, now=1000.0 + files.SIGNED_URL_TTL_SECONDS + 1)
    assert not files.verify_download(attachment, expires + 10_000, signature, now=1000.0)
    assert not files.verify_download(attachment, expires, "0" * 64, now=1000.0)


def _ticket(**overrides):
    ticket = {"id": uuid4(), "number": 42, "subject": "s", "category": "bug", "priority": "normal",
              "status": "open", "created_at": NOW, "updated_at": NOW, "requester_user_id": "requester",
              "requester_name": "Ann", "requester_email": "ann@example.invalid", "related_campaign_id": None,
              "related_call_id": None, "csat_score": None, "csat_comment": None, "resolved_at": None,
              "assignee_user_id": None, "first_response_due_at": NOW + timedelta(hours=1),
              "resolution_due_at": NOW + timedelta(hours=8), "first_responded_at": None,
              "first_response_breached_at": None, "resolution_breached_at": None, "version": 3,
              "diagnostics": {"findings": []}}
    ticket.update(overrides)
    return ticket


def _message(author_type, body, internal=False, name="Real Staff Name"):
    return {"id": uuid4(), "author_type": author_type, "body": body, "is_internal": internal,
            "created_at": NOW, "author_name": name, "author_user_id": uuid4()}


def test_customer_projection_hides_internal_notes_and_staff_identity():
    messages = [_message("customer", "help", name="Ann"), _message("staff", "secret", internal=True),
                _message("staff", "public answer")]
    view = projection.customer_ticket(_ticket(), messages, [], viewer_id="requester", now=NOW)
    assert [m["body"] for m in view["messages"]] == ["help", "public answer"]
    assert view["messages"][1]["author_name"] == "Talk-Lee Support"
    assert "is_internal" not in view["messages"][1]
    assert view["number"] == "SUP-000042"
    for staff_only in ("version", "diagnostics", "sla", "events", "tenant", "assignee"):
        assert staff_only not in view
    assert "email" not in view["requester"]


def test_staff_projection_keeps_internal_notes_and_sla():
    messages = [_message("staff", "secret", internal=True)]
    view = projection.staff_ticket(_ticket(first_response_due_at=NOW - timedelta(minutes=1)), messages, [], now=NOW)
    assert view["messages"][0]["is_internal"] is True
    assert view["messages"][0]["author_name"] == "Real Staff Name"
    assert view["sla"]["breached"] is True and view["version"] == 3


def test_cursor_round_trip_and_validation():
    record = uuid4()
    assert decode_cursor(encode_cursor(NOW, record)) == (NOW, record)
    assert decode_cursor(None) is None
    for bad in ("not-base64!!", encode_cursor(NOW, record)[:-4] + "AAAA"):
        with pytest.raises(SupportValidationError):
            decode_cursor(bad)
    rows = [{"updated_at": NOW, "id": uuid4()} for _ in range(3)]
    assert next_cursor(rows, 3) is None
    assert decode_cursor(next_cursor(rows, 2)) == (NOW, rows[1]["id"])


def test_receipt_json_is_stable():
    value = {"id": uuid4(), "at": NOW, "nested": [{"when": NOW}]}
    body = receipts.to_json(value)
    assert body["at"] == NOW.isoformat() and body["id"] == str(value["id"])
    assert receipts.to_json(body) == body


def test_page_context_keeps_only_well_formed_fields():
    campaign = str(uuid4())
    clean = conversation_service.clean_page_context({
        "route": "/campaigns/[id]", "campaign_id": campaign, "call_id": "nope", "app_version": "1.2.3",
        "viewport": "1440x900", "tenant_id": str(uuid4()), "extra": "x"})
    assert clean == {"route": "/campaigns/[id]", "campaign_id": campaign, "app_version": "1.2.3",
                     "viewport": "1440x900"}
    assert conversation_service.clean_page_context({"route": "javascript:alert(1)"}) == {}


def test_metric_labels_are_bounded():
    support_metrics.escalation("made-up-reason")
    support_metrics.ticket_created("not-a-category", "critical")
    support_metrics.bot_error("weird")
    support_metrics.sla_breach("bogus")
    labels = {sample.labels.get("reason") for sample in support_metrics.ESCALATIONS.collect()[0].samples}
    assert "made-up-reason" not in labels and "other" in labels


@pytest.mark.parametrize("minute,forced,expected", [(0, False, True), (14, False, True), (15, False, False),
                                                   (45, False, False), (45, True, True)])
def test_hourly_pass_runs_in_the_first_quarter_or_when_forced(minute, forced, expected):
    assert should_run_hourly(NOW.replace(minute=minute), forced) is expected


async def test_worker_does_nothing_while_switched_off(monkeypatch):
    from app.workers import support_jobs

    monkeypatch.delenv("SUPPORT_ENABLED", raising=False)
    assert await support_jobs.main([]) == 0


def test_staff_alert_email_escapes_customer_text():
    from app.domain.services.support import notifications

    manager = notifications._register_templates()
    rendered = manager.render_email("support_staff_alert", number="SUP-000001", priority="high", category="bug",
                                    headline="New high support ticket", link="https://x/y", tenant="<b>T</b>",
                                    subject="<script>x</script>")
    assert "<script>" not in rendered.body_html and "&lt;script&gt;" in rendered.body_html


def test_bot_answers_keep_numbered_steps_on_separate_lines():
    import json

    from app.domain.services.support.bot.result import parse_final_result

    raw = json.dumps({"answer": "I checked it.\r\n\n\n\n1.  Open   Campaigns\n2. Fix the hours\x07", "sources": [],
                      "resolution_prompt": True, "needs_clarification": False, "propose_escalation": False,
                      "escalation_reason": "", "category": "calls_campaigns"})
    assert parse_final_result(raw, allowed_sources={})["answer"] == "I checked it.\n\n1. Open Campaigns\n2. Fix the hours"
