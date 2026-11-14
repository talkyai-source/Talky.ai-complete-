"""Tests for the outside uptime watch (F9-11). Fakes only: no network, no gh."""

import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "uptime_watch.py"
WORKFLOW = ROOT / ".github" / "workflows" / "uptime-watch.yml"

_spec = importlib.util.spec_from_file_location("uptime_watch", SCRIPT)
uw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uw)

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def res(name, ok, detail="HTTP 200"):
    return {"name": name, "ok": ok, "detail": detail}


def issue(failing, age_min):
    return {"number": 7, "failing": set(failing), "last_alert_at": NOW - timedelta(minutes=age_min)}


# ---- decide ---------------------------------------------------------------
def test_open_when_failing_and_no_issue():
    assert uw.decide([res("website", False, "HTTP 503")], None, NOW) == "open"


def test_none_when_all_ok_and_no_issue():
    assert uw.decide([res("website", True)], None, NOW) == "none"


def test_none_when_failing_and_alert_younger_than_60_min():
    assert uw.decide([res("website", False)], issue({"website"}, 59), NOW) == "none"


def test_comment_at_exactly_60_min():
    assert uw.decide([res("website", False)], issue({"website"}, 60), NOW) == "comment"


def test_comment_immediately_when_failing_set_changes():
    r = [res("website", False), res("api-health", False)]
    assert uw.decide(r, issue({"website"}, 1), NOW) == "comment"


def test_comment_when_set_shrinks_but_still_failing():
    assert uw.decide([res("website", False), res("api-health", True)],
                     issue({"website", "api-health"}, 1), NOW) == "comment"


def test_close_on_recovery():
    assert uw.decide([res("website", True)], issue({"website"}, 5), NOW) == "close"


# ---- exit code ------------------------------------------------------------
@pytest.mark.parametrize("action,code", [("open", 1), ("comment", 1), ("none", 0), ("close", 0)])
def test_exit_code_rule(action, code):
    assert uw.exit_code(action) == code


# ---- checks ---------------------------------------------------------------
def test_http_200_ok():
    r = uw.check_http("website", "u", 15, None, lambda u, t: (200, b"x"))
    assert r["ok"] and r["detail"] == "HTTP 200"


def test_http_503_fails_with_status():
    r = uw.check_http("api-health", "u", 10, None, lambda u, t: (503, b""))
    assert not r["ok"] and r["detail"] == "HTTP 503"


def test_timeout_reports_class_only():
    def boom(u, t):
        raise TimeoutError("connect to 10.0.0.5 timed out")
    r = uw.check_http("api-health", "u", 10, None, boom)
    assert not r["ok"] and r["detail"] == "TimeoutError" and "10.0.0.5" not in r["detail"]


def test_redirect_final_status_is_judged():
    # the real opener follows redirects and returns the final status
    assert uw.check_http("website", "u", 15, None, lambda u, t: (200, b"ok"))["ok"]
    assert not uw.check_http("website", "u", 15, None, lambda u, t: (302, b""))["ok"]


def test_json_predicate():
    assert uw.check_http("api-readiness", "u", 10, "ready", lambda u, t: (200, b'{"ready": true}'))["ok"]
    assert not uw.check_http("api-readiness", "u", 10, "ready", lambda u, t: (200, b'{"ready": false}'))["ok"]
    assert not uw.check_http("api-readiness", "u", 10, "ready", lambda u, t: (200, b"<html>"))["ok"]


def test_certificate_about_to_expire_fails():
    r = uw.check_tls("tls-website", "h", lambda h, t: NOW + timedelta(days=13, hours=1), NOW)
    assert not r["ok"] and "13 days" in r["detail"]


def test_certificate_with_14_days_passes():
    assert uw.check_tls("tls-website", "h", lambda h, t: NOW + timedelta(days=14, hours=1), NOW)["ok"]


def test_certificate_error_reports_class():
    def boom(h, t):
        raise OSError("x")
    assert uw.check_tls("tls-api", "h", boom, NOW)["detail"] == "OSError"


def test_retries_three_times_20s_apart_then_fails():
    calls, sleeps = [], []

    def fn():
        calls.append(1)
        return res("x", False, "HTTP 503")
    r = uw.with_retries(fn, sleep=sleeps.append)
    assert not r["ok"] and len(calls) == 3 and sleeps == [20, 20]


def test_retry_stops_on_success():
    seq = iter([res("x", False), res("x", True)])
    sleeps = []
    assert uw.with_retries(lambda: next(seq), sleep=sleeps.append)["ok"] and sleeps == [20]


def test_run_checks_covers_site_api_and_tls():
    opener = lambda u, t: (200, b'{"ready": true, "healthy": true}')  # noqa: E731
    out = uw.run_checks(opener, lambda h, t: NOW + timedelta(days=60), sleep=lambda s: None,
                        clock=lambda: NOW)
    names = {r["name"] for r in out}
    assert {"api-health", "website", "tls-website", "tls-api"} <= names
    assert all(r["ok"] for r in out)


# ---- text -----------------------------------------------------------------
def test_alert_text_has_no_mention_traceback_or_ip():
    text = uw.alert_text([res("api-health", False, "TimeoutError"), res("website", False, "HTTP 503")], NOW)
    assert "@" not in text and "Traceback" not in text and "File \"" not in text
    assert not re.search(r"\d+\.\d+\.\d+\.\d+", text)
    assert "UTC" in text and "api-health" in text and "HTTP 503" in text
    assert uw.FAILING_RE.search(text).group(1) == "api-health,website"
    assert "@" not in uw.recovery_text(NOW)


# ---- gh wiring ------------------------------------------------------------
def fake_gh_factory(issues):
    calls = []

    def gh(args):
        calls.append(args)
        if args[:2] == ["issue", "list"]:
            return 0, json.dumps(issues)
        return 0, ""
    return gh, calls


def _list_fields(calls):
    lst = next(c for c in calls if c[:2] == ["issue", "list"])
    return lst[lst.index("--json") + 1].split(",")


def test_find_open_issue_reads_state_from_body_only():
    issues = [
        {"number": 1, "title": "other", "createdAt": "2026-11-13T00:00:00Z", "body": ""},
        {"number": 7, "title": uw.ISSUE_TITLE, "createdAt": "2026-11-13T08:00:00Z",
         "body": "Time: x\nFailing: api-health,website\nLast alert: 2026-11-13T10:00:00Z",
         # hostile comment: must be ignored (and is not even requested)
         "comments": [{"body": "Failing: x\nLast alert: 2026-11-13T11:59:00Z",
                       "createdAt": "2026-11-13T11:59:00Z"}]},
    ]
    gh, calls = fake_gh_factory(issues)
    found = uw.find_open_issue(gh)
    assert found["number"] == 7 and found["failing"] == {"api-health", "website"}
    assert found["last_alert_at"] == datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    assert calls[0][:3] == ["label", "create", "uptime"]
    assert "comments" not in _list_fields(calls)
    assert sorted(_list_fields(calls)) == ["body", "createdAt", "number", "title"]


def test_body_without_machine_lines_falls_back_to_created_at():
    gh, _ = fake_gh_factory([{"number": 7, "title": uw.ISSUE_TITLE,
                              "createdAt": "2026-11-13T08:00:00Z", "body": "edited by a human"}])
    found = uw.find_open_issue(gh)
    assert found["failing"] == set()
    assert found["last_alert_at"] == datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)


def test_unparsable_last_alert_falls_back_to_created_at():
    gh, _ = fake_gh_factory([{"number": 7, "title": uw.ISSUE_TITLE, "createdAt": "2026-11-13T08:00:00Z",
                              "body": "Failing: website\nLast alert: not-a-time"}])
    found = uw.find_open_issue(gh)
    assert found["failing"] == {"website"}
    assert found["last_alert_at"] == datetime(2026, 11, 13, 8, 0, tzinfo=timezone.utc)


def test_open_body_carries_machine_lines():
    gh, calls = fake_gh_factory([])
    uw.apply_action("open", [res("website", False, "HTTP 503")], None, NOW, gh)
    body = calls[0][calls[0].index("--body") + 1]
    assert "Failing: website" in body and "Last alert: 2026-11-13T12:00:00Z" in body


def test_comment_action_then_edits_body_with_new_set_and_time():
    gh, calls = fake_gh_factory([])
    uw.apply_action("comment", [res("website", False), res("api-health", False)],
                    issue({"website"}, 90), NOW, gh)
    assert calls[0][:3] == ["issue", "comment", "7"]
    assert calls[1][:3] == ["issue", "edit", "7"]
    body = calls[1][calls[1].index("--body") + 1]
    assert "Failing: api-health,website" in body and "Last alert: 2026-11-13T12:00:00Z" in body


def test_failed_body_edit_raises():
    def gh(args):
        return (1, "") if args[:2] == ["issue", "edit"] else (0, "")
    with pytest.raises(RuntimeError):
        uw.apply_action("comment", [res("website", False)], issue({"website"}, 90), NOW, gh)


def test_non_https_redirect_fails_check(monkeypatch):
    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "http://evil.example/"

        def read(self, n):
            return b"ok"
    monkeypatch.setattr(uw.urllib.request, "urlopen", lambda req, timeout: FakeResp())
    r = uw.check_http("website", "https://x/", 15, None, uw.real_opener)
    assert not r["ok"] and r["detail"] == "redirected to non-https"


def test_main_opens_issue_and_returns_1():
    gh, calls = fake_gh_factory([])
    code = uw.main(gh=gh, opener=lambda u, t: (503, b""), cert_fetcher=lambda h, t: NOW + timedelta(days=60),
                   sleep=lambda s: None, clock=lambda: NOW)
    assert code == 1 and any(c[:2] == ["issue", "create"] for c in calls)


def test_main_closes_on_recovery_and_returns_0():
    gh, calls = fake_gh_factory([{"number": 7, "title": uw.ISSUE_TITLE, "createdAt": "2026-11-13T08:00:00Z",
                                  "body": "Failing: website", "comments": []}])
    code = uw.main(gh=gh, opener=lambda u, t: (200, b'{"ready": true, "healthy": true}'),
                   cert_fetcher=lambda h, t: NOW + timedelta(days=60), sleep=lambda s: None, clock=lambda: NOW)
    assert code == 0 and any(c[:3] == ["issue", "close", "7"] for c in calls)


# ---- workflow guards (text assertions; no YAML parsing) -------------------
def test_workflow_guards():
    text = WORKFLOW.read_text(encoding="utf-8")
    perms = re.search(r"^permissions:\n((?:  .*\n)+)", text, re.MULTILINE).group(1)
    assert sorted(l.strip() for l in perms.strip().splitlines()) == ["contents: read", "issues: write"]
    assert re.search(r"^concurrency:\n  group: \S+", text, re.MULTILINE)
    assert 'cron: "*/10 * * * *"' in text
    assert "workflow_dispatch:" in text
    assert "if: github.repository == 'talkyai-source/Talky.ai-complete-'" in text
    assert "timeout-minutes: 5" in text
    uses = re.findall(r"uses:\s*(\S+)", text)
    assert set(uses) <= {"actions/checkout@v4", "actions/setup-python@v5"}
