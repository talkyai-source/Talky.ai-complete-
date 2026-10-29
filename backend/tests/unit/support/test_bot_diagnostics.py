import sys
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.domain.services.support.bot import diagnostics as d

T, U, OTHER = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


class FakeConn:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def _pick(self, sql):
        for key, val in self.rows.items():
            if key in sql:
                return val
        return None

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        v = self._pick(sql)
        if isinstance(v, list):
            return v[0] if v else None
        return v

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        v = self._pick(sql)
        return v if isinstance(v, list) else []


def make(monkeypatch, rows, minutes=None, redis=None):
    conn = FakeConn(rows)

    @asynccontextmanager
    async def acq(pool, tenant):
        assert tenant == T
        yield conn

    monkeypatch.setattr(d, "acquire_with_tenant", acq)

    async def mins(c, t):
        assert t == T
        return minutes or SimpleNamespace(state="known", exhausted=False, as_dict=lambda: {"remaining_minutes": 5})

    monkeypatch.setattr(d, "compute_minutes_status", mins)
    return d.ToolExecutor(pool=object(), redis=redis, tenant_id=T, user_id=U), conn


def assert_scoped(conn):
    assert conn.calls
    for sql, args in conn.calls:
        if "FROM plans" in sql:
            continue
        assert "tenant_id" in sql or "FROM tenants WHERE id = $1" in sql, sql
        assert T in args, sql
        assert OTHER not in args


async def test_account_overview_findings(monkeypatch):
    ex, conn = make(monkeypatch, {"FROM tenants": {"plan_id": "p", "subscription_status": "canceled", "status": "suspended"},
                                  "FROM plans": {"name": "Basic", "price": 99}},
                    minutes=SimpleNamespace(state="known", exhausted=True, as_dict=lambda: {}))
    out = await ex.execute("get_account_overview", {})
    assert {f.code for f in out.findings} == {"account_suspended", "out_of_minutes", "subscription_inactive"}
    assert out.account["paying"] is False
    assert all(f.account_wide_block for f in out.findings)
    assert_scoped(conn)


async def test_paying(monkeypatch):
    ex, _ = make(monkeypatch, {"FROM tenants": {"plan_id": "p", "subscription_status": "active", "status": "active"},
                               "FROM plans": {"name": "Basic", "price": 99}})
    assert (await ex.execute("get_account_overview", {})).account["paying"] is True


async def test_foreign_campaign_by_uuid_and_name_bind_executor_tenant(monkeypatch):
    ex, conn = make(monkeypatch, {})
    for ref in (str(uuid.uuid4()), "Their Campaign"):
        out = await ex.execute("get_campaign_status", {"campaign": ref})
        assert out.result == {"error": "not_found"}
    assert_scoped(conn)
    assert all(args[-1] == T for _, args in conn.calls)


async def test_model_supplied_tenant_rejected(monkeypatch):
    ex, conn = make(monkeypatch, {})
    out = await ex.execute("get_recent_call_issues", {"tenant_id": OTHER})
    assert out.result == {"error": "invalid_arguments"} and conn.calls == []


async def test_campaign_block_and_failures(monkeypatch):
    class R:
        async def get(self, key):
            return b'{"code": "caller_id_not_verified"}'

    cid = str(uuid.uuid4())
    ex, conn = make(monkeypatch, {
        "FROM campaigns": {"id": cid, "name": "C", "status": "running", "direction": "outbound", "updated_at": None},
        "FROM dialer_jobs": [{"failure_reason": "out_of_minutes", "failure_category": None, "n": 3}]}, redis=R())
    out = await ex.execute("get_campaign_status", {"campaign": cid})
    assert out.findings[0].staff_only and out.findings[0].related_campaign_id == cid
    assert out.result["failures_24h"][0]["count"] == 3
    assert_scoped(conn)


async def test_call_details_no_sensitive_columns(monkeypatch):
    ex, conn = make(monkeypatch, {"FROM calls": {
        "id": "x", "campaign_id": None, "status": "completed", "outcome": "ok", "direction": "outbound",
        "started_at": None, "ended_at": None, "duration_seconds": 4, "dialer_job_id": None,
        "transcript": "SECRETTEXT", "recording_url": "http://r"}})
    out = await ex.execute("get_call_details", {"call_id": str(uuid.uuid4())})
    blob = str(out.result)
    assert "SECRETTEXT" not in blob and "recording" not in blob and "transcript" not in blob
    for sql, _ in conn.calls:
        assert "transcript" not in sql and "recording_url" not in sql and "summary" not in sql
    assert_scoped(conn)


async def test_foreign_call_not_found(monkeypatch):
    ex, _ = make(monkeypatch, {})
    assert (await ex.execute("get_call_details", {"call_id": str(uuid.uuid4())})).result == {"error": "not_found"}


async def test_phones_masked_and_finding(monkeypatch):
    ex, conn = make(monkeypatch, {"FROM tenant_phone_numbers": [
        {"e164": "+442046132300", "status": "pending", "verified_at": None, "provider": "x"}]})
    out = await ex.execute("get_phone_numbers_status", {})
    assert "442046132300" not in str(out.result) and "2300" in str(out.result)
    assert out.findings[0].code == "caller_id_not_verified" and out.findings[0].staff_only
    assert_scoped(conn)


async def test_connectors_never_select_config(monkeypatch):
    ex, conn = make(monkeypatch, {"FROM connectors": [
        {"type": "crm", "provider": "p", "name": "n", "status": "ok", "provider_revocation_status": None,
         "config": "CFGSECRET"}]})
    out = await ex.execute("get_connectors_status", {})
    assert "config" not in conn.calls[0][0] and "CFGSECRET" not in str(out.result)
    assert_scoped(conn)


async def test_tickets_scoped_to_user(monkeypatch):
    ex, conn = make(monkeypatch, {"FROM support_tickets": [
        {"id": "i", "number": 7, "subject": "s", "status": "open", "category": "bug"}]})
    out = await ex.execute("find_open_tickets", {})
    assert out.result["tickets"][0]["number"] == "SUP-000007"
    assert conn.calls[0][1] == (T, U)


async def test_db_error_and_unknown_tool(monkeypatch):
    ex, conn = make(monkeypatch, {})

    async def boom(*a):
        raise RuntimeError("secret detail")

    conn.fetch = boom
    out = await ex.execute("get_connectors_status", {})
    assert out.result == {"error": "check_unavailable"} and "secret" not in out.trace.summary
    assert (await ex.execute("refund", {})).result == {"error": "unknown_tool"}


async def test_search_articles_sources(monkeypatch):
    async def fake(pool, query, *, limit=5, tenant_id=None):
        assert limit == 3 and tenant_id == T
        return [{"slug": "s", "title": "T", "section": "x", "snippet": "y" * 500, "url": "/docs/s"}]

    monkeypatch.setitem(sys.modules, "app.domain.services.support.articles",
                        SimpleNamespace(search_articles=fake))
    ex, _ = make(monkeypatch, {})
    out = await ex.execute("search_help_articles", {"query": "hello"})
    assert out.sources[0].slug == "s" and len(out.result["articles"][0]["snippet"]) == 200
