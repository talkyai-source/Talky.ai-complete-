#!/usr/bin/env python3
"""Outside uptime watch (QA F9-11). Standard library only.

Runs from a GitHub Actions runner, checks the public site and API without any
credentials, and opens / updates / closes one GitHub issue through the `gh` CLI
(authenticated by GH_TOKEN). The repository is public: issue text carries only
check name, HTTP status or error class, and a UTC time.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

ISSUE_TITLE = "Outside check: site or API is not answering"
ISSUE_LABEL = "uptime"
REPEAT_MINUTES = 60
RETRIES = 3
RETRY_DELAY_S = 20
MIN_CERT_DAYS = 14
FAILING_RE = re.compile(r"^Failing:\s*(.*)$", re.MULTILINE)
LAST_ALERT_RE = re.compile(r"^Last alert:\s*(\S+)\s*$", re.MULTILINE)
RECOVERED_MARK = "Recovered:"


class NonHttpsRedirect(Exception):
    pass

SITE_HOST = "talkleeai.com"
API_HOST = "api.talkleeai.com"

# name, url, timeout seconds, JSON predicate key (None = status only)
HTTP_CHECKS = [
    ("api-health", f"https://{API_HOST}/health", 10, None),
    ("website", f"https://{SITE_HOST}/", 15, None),
    ("api-readiness", f"https://{API_HOST}/api/v1/healthz/deep", 10, "ready"),
    ("api-workers", f"https://{API_HOST}/api/v1/healthz/workers", 10, "healthy"),
]
TLS_CHECKS = [("tls-website", SITE_HOST), ("tls-api", API_HOST)]


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def real_opener(url: str, timeout: float):
    """GET url, following redirects. Returns (status, body bytes)."""
    req = urllib.request.Request(url, headers={"User-Agent": "talky-uptime-watch/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            if not resp.geturl().startswith("https://"):
                raise NonHttpsRedirect()
            return resp.status, resp.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


def real_cert_fetcher(host: str, timeout: float) -> datetime:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            cert = tls.getpeercert()
    return datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)


def check_http(name, url, timeout, json_key, opener):
    try:
        status, body = opener(url, timeout)
    except NonHttpsRedirect:
        return {"name": name, "ok": False, "detail": "redirected to non-https"}
    except Exception as exc:  # error CLASS only, never the message
        return {"name": name, "ok": False, "detail": type(exc).__name__}
    if status != 200:
        return {"name": name, "ok": False, "detail": f"HTTP {status}"}
    if json_key is not None:
        try:
            ok = json.loads(body).get(json_key) is True
        except Exception:
            return {"name": name, "ok": False, "detail": "HTTP 200 unexpected body"}
        if not ok:
            return {"name": name, "ok": False, "detail": "HTTP 200 not ready"}
    return {"name": name, "ok": True, "detail": "HTTP 200"}


def check_tls(name, host, cert_fetcher, now):
    try:
        expires = cert_fetcher(host, 10)
    except Exception as exc:
        return {"name": name, "ok": False, "detail": type(exc).__name__}
    days = (expires - now).days
    if days < MIN_CERT_DAYS:
        return {"name": name, "ok": False, "detail": f"certificate expires in {days} days"}
    return {"name": name, "ok": True, "detail": f"certificate valid {days} days"}


def with_retries(fn, retries=RETRIES, delay=RETRY_DELAY_S, sleep=time.sleep):
    result = fn()
    for _ in range(retries - 1):
        if result["ok"]:
            break
        sleep(delay)
        result = fn()
    return result


def run_checks(opener=real_opener, cert_fetcher=real_cert_fetcher, sleep=time.sleep,
               clock=lambda: datetime.now(timezone.utc)):
    # Run in parallel: worst case per check is ~70 s, serial would exceed the 5 min job limit.
    jobs = [lambda n=name, u=url, t=timeout, k=key: check_http(n, u, t, k, opener)
            for name, url, timeout, key in HTTP_CHECKS]
    jobs += [lambda n=name, h=host: check_tls(n, h, cert_fetcher, clock())
             for name, host in TLS_CHECKS]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(with_retries, job, sleep=sleep) for job in jobs]
        return [f.result() for f in futures]


# --------------------------------------------------------------------------
# Decision (pure)
# --------------------------------------------------------------------------
def decide(results, open_issue, now):
    """open_issue: None or {"last_alert_at": datetime, "failing": set[str]}."""
    failing = {r["name"] for r in results if not r["ok"]}
    if not failing:
        return "close" if open_issue is not None else "none"
    if open_issue is None:
        return "open"
    if failing != set(open_issue["failing"]):
        return "comment"
    if now - open_issue["last_alert_at"] >= timedelta(minutes=REPEAT_MINUTES):
        return "comment"
    return "none"


def exit_code(action):
    return 1 if action in ("open", "comment") else 0


def _utc(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def alert_text(results, now):
    bad = [r for r in results if not r["ok"]]
    lines = [f"Time: {_utc(now)}", "", "Failing checks:"]
    lines += [f"- {r['name']}: {r['detail']}" for r in bad]
    lines += ["", "Failing: " + ",".join(sorted(r["name"] for r in bad))]
    return "\n".join(lines)


def recovery_text(now):
    return f"{RECOVERED_MARK} all outside checks pass again.\nTime: {_utc(now)}"


# --------------------------------------------------------------------------
# GitHub via gh CLI
# --------------------------------------------------------------------------
def real_gh(args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout


def _parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def body_text(results, now):
    """Issue body = the only alert state we trust (comments are writable by anyone)."""
    iso = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return alert_text(results, now) + f"\nLast alert: {iso}"


def find_open_issue(gh):
    gh(["label", "create", ISSUE_LABEL, "--color", "D93F0B",
        "--description", "Outside uptime watch"])  # fails harmlessly if it exists
    # Comments are deliberately NOT requested: the repository is public.
    code, out = gh(["issue", "list", "--label", ISSUE_LABEL, "--state", "open", "--limit", "20",
                    "--json", "number,title,createdAt,body"])
    if code != 0:
        raise RuntimeError("gh issue list failed")
    for issue in json.loads(out or "[]"):
        if issue.get("title") != ISSUE_TITLE:
            continue
        text = issue.get("body") or ""
        try:
            last = _parse_ts(issue["createdAt"])
        except Exception:
            last = datetime.min.replace(tzinfo=timezone.utc)
        m = LAST_ALERT_RE.search(text)
        if m:
            try:
                last = _parse_ts(m.group(1).strip())
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
            except Exception:
                pass  # keep the createdAt fallback
        match = FAILING_RE.search(text)
        failing = {x.strip() for x in (match.group(1).split(",") if match else []) if x.strip()}
        return {"number": issue["number"], "last_alert_at": last, "failing": failing}
    return None


def apply_action(action, results, issue, now, gh):
    if action == "open":
        code, _ = gh(["issue", "create", "--title", ISSUE_TITLE, "--label", ISSUE_LABEL,
                      "--body", body_text(results, now)])
    elif action == "comment":
        code, _ = gh(["issue", "comment", str(issue["number"]), "--body", alert_text(results, now)])
        if code == 0:
            code, _ = gh(["issue", "edit", str(issue["number"]), "--body", body_text(results, now)])
    elif action == "close":
        code, _ = gh(["issue", "close", str(issue["number"]), "--comment", recovery_text(now)])
    else:
        return
    if code != 0:
        raise RuntimeError(f"gh {action} failed")


def main(gh=real_gh, opener=real_opener, cert_fetcher=real_cert_fetcher, sleep=time.sleep,
         clock=lambda: datetime.now(timezone.utc)):
    results = run_checks(opener, cert_fetcher, sleep, clock)
    for r in results:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['name']}: {r['detail']}")
    now = clock()
    issue = find_open_issue(gh)
    action = decide(results, issue, now)
    apply_action(action, results, issue, now, gh)
    print(f"action: {action}")
    return exit_code(action)


if __name__ == "__main__":
    sys.exit(main())
