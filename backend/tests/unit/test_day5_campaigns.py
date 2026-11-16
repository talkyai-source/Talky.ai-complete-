"""Day 5 campaign items: D5-25 list phase, D5-29 calling rules, D3-13 first speaker.

D5-26 (a ``completed`` writer) is deliberately not built here; the one test below
only pins what Start already does with a ``completed`` campaign.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.routing import Match

from app.api.v1.endpoints import campaigns as ep
from app.api.v1.endpoints.telephony_bridge import CAMPAIGN_LIVE_CALL_STATUSES
from app.api.v1.schemas.campaigns import (
    CampaignCreateRequest, CampaignStartRequest, CampaignUpdateRequest,
)
from app.domain.services import campaign_service as svc
from app.domain.services.dialer.campaign_schedule import describe_schedule
from tests.unit.test_campaign_visibility_w5 import (
    CAMPAIGN, TENANT, _FakeDB, _campaign_row, _patch_update_dependencies, _update_body,
)

OTHER_TENANT = "99999999-9999-9999-9999-999999999999"


def _cid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


# ---------------------------------------------------------------------------
# D5-25: the list carries the same progress the detail carries
# ---------------------------------------------------------------------------
class _Db:
    """One fake connection serving both the detail's and the list's reads."""

    def __init__(self, rules=None, live=None, awaiting=None, fail=False):
        self.rules = rules
        self.live = live or {}
        self.awaiting = awaiting or {}
        self.fail = fail
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        if "calling_rules" in query:
            return self.rules
        if "FROM calls" in query:
            return self.live.get(str(args[1]), 0)
        if "FROM dialer_jobs" in query:
            return self.awaiting.get(str(args[1]), 0)
        return None

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        if self.fail:
            raise RuntimeError("synthetic read failure")
        if "leads" in query:
            return []  # the detail's contact timezones
        ids = args[1]
        source = self.live if "FROM calls" in query else self.awaiting
        return [{"campaign_id": cid, "n": source[str(cid)]} for cid in ids if source.get(str(cid))]


@pytest.fixture
def wired(monkeypatch):
    holder = SimpleNamespace(db=_Db())

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        holder.tenants = getattr(holder, "tenants", []) + [tenant_id]
        yield holder.db

    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(svc, "acquire_with_tenant", acquire)
    return holder


class _ListQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, *_a, **_k):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def neq(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        return SimpleNamespace(data=[dict(r) for r in self.rows], error=None)


def _list_client(rows):
    return SimpleNamespace(pool=object(), table=lambda name: _ListQuery(rows))


async def _list(rows, tenant=TENANT):
    return await ep.list_campaigns(
        request=SimpleNamespace(), current_user=SimpleNamespace(id="u1", tenant_id=tenant),
        db_client=_list_client(rows))


async def _detail(row):
    class Service:
        async def get_campaign(self, campaign_id):
            return row

    original = ep._get_campaign_service
    ep._get_campaign_service = lambda _db: Service()
    try:
        return await ep.get_campaign(
            campaign_id=row["id"], request=SimpleNamespace(),
            current_user=SimpleNamespace(id="u1", tenant_id=TENANT),
            db_client=SimpleNamespace(pool=object()))
    finally:
        ep._get_campaign_service = original


@pytest.mark.parametrize("status,live,awaiting,phase", [
    ("running", 0, 4, "preparing"),
    ("running", 2, 0, "running"),
    ("stopped", 3, 0, "stopping"),
    ("paused", 1, 0, "pausing"),
    ("stopped", 0, 0, "stopped"),
    ("draft", 0, 0, "draft"),
])
async def test_list_row_progress_equals_the_detail_progress(wired, status, live, awaiting, phase):
    cid = _cid(1)
    wired.db = _Db(live={cid: live}, awaiting={cid: awaiting})
    row = {"id": cid, "tenant_id": TENANT, "status": status, "name": "c"}

    listed = (await _list([row]))["campaigns"][0]
    detail = await _detail(row)

    assert listed["progress"]["phase"] == phase
    assert listed["progress"] == detail["progress"]
    assert listed["status"] == status  # the stored status is untouched
    assert listed["name"] == "c"       # existing fields keep their meaning


async def test_a_running_campaign_with_jobs_waiting_reads_preparing(wired):
    cid = _cid(2)
    wired.db = _Db(awaiting={cid: 250})
    out = await _list([{"id": cid, "tenant_id": TENANT, "status": "running"}])
    assert out["campaigns"][0]["progress"] == {
        "live_calls": 0, "jobs_awaiting_dispatch": 250, "phase": "preparing"}


async def test_two_statements_serve_the_whole_page_with_the_tenant_bound(wired):
    """Contract change (lead's request): calls, then dialer_jobs, each grouped by campaign;
    never one per campaign, and no UNION so the SQL scanner reads both as scoped."""
    ids = [_cid(n) for n in range(1, 31)]
    rows = [{"id": c, "tenant_id": TENANT, "status": "running"} for c in ids]
    wired.db = _Db(awaiting={ids[3]: 5}, live={ids[4]: 1})

    out = await _list(rows)

    assert len(wired.db.fetch_calls) == 2 and wired.db.fetchval_calls == []
    calls_sql, jobs_sql = (q for q, _ in wired.db.fetch_calls)
    assert "FROM calls" in calls_sql and "FROM dialer_jobs" in jobs_sql
    for query, args in wired.db.fetch_calls:
        assert "UNION" not in query and "tenant_id = $1" in query and "campaign_id = ANY($2" in query
        assert "GROUP BY campaign_id" in query
        assert args[0] == TENANT and list(args[1]) == ids
    assert list(wired.db.fetch_calls[0][1][2]) == list(CAMPAIGN_LIVE_CALL_STATUSES)
    assert wired.tenants == [TENANT]
    phases = [r["progress"]["phase"] for r in out["campaigns"]]
    assert phases[3] == "preparing" and phases.count("running") == 29


async def test_an_empty_page_runs_no_statement(wired):
    assert await _list([]) == {"campaigns": []}
    assert wired.db.fetch_calls == []


async def test_an_unreadable_count_does_not_break_the_list(wired):
    cid = _cid(3)
    wired.db = _Db(fail=True)
    out = await _list([{"id": cid, "tenant_id": TENANT, "status": "running", "name": "x"}])
    assert out["campaigns"][0]["name"] == "x"
    assert out["campaigns"][0]["progress"] is None


async def test_another_tenant_gets_only_its_own_ids_bound(wired):
    cid = _cid(4)
    wired.db = _Db(awaiting={cid: 1})
    await _list([{"id": cid, "tenant_id": OTHER_TENANT, "status": "running"}], tenant=OTHER_TENANT)
    assert wired.db.fetch_calls[0][1][0] == OTHER_TENANT


# ---------------------------------------------------------------------------
# D5-29: GET /campaigns/calling-rules
# ---------------------------------------------------------------------------
_ACCOUNT_RULES = {"timezone": "Europe/London", "time_window_start": "10:00",
                  "time_window_end": "16:00", "allowed_days": [0, 1, 2, 3, 4]}


async def _rules(wired, rules):
    wired.db = _Db(rules=rules)
    return await ep.get_calling_rules(
        current_user=SimpleNamespace(id="u1", tenant_id=TENANT),
        db_client=SimpleNamespace(pool=object()))


@pytest.mark.parametrize("rules", [_ACCOUNT_RULES, None, json.dumps(_ACCOUNT_RULES)])
async def test_calling_rules_equal_the_detail_schedule_of_a_scheduleless_campaign(wired, rules):
    out = await _rules(wired, rules)
    wired.db = _Db(rules=rules)
    row = {"id": _cid(9), "tenant_id": TENANT, "status": "draft", "calling_config": {}}
    detail = await _detail(row)
    assert out["effective_schedule"] == detail["effective_schedule"]
    assert out["effective_schedule"] == describe_schedule(rules, None, {})


async def test_calling_rules_show_account_hours_default_and_call_anytime_switch(wired, monkeypatch):
    own = (await _rules(wired, _ACCOUNT_RULES))["effective_schedule"]
    assert (own["timezone"], own["time_window_start"], own["allowed_days"]) == (
        "Europe/London", "10:00", [0, 1, 2, 3, 4])
    assert own["sources"]["timezone"] == "account"

    default = (await _rules(wired, None))["effective_schedule"]
    assert default["sources"]["timezone"] == "platform_default"
    assert default["account_rules"]["configured"] is False

    monkeypatch.setenv("DIALER_ALLOW_IGNORE_SCHEDULE", "false")
    assert (await _rules(wired, None))["effective_schedule"]["ignore_schedule_allowed"] is False
    monkeypatch.setenv("DIALER_ALLOW_IGNORE_SCHEDULE", "true")
    assert (await _rules(wired, None))["effective_schedule"]["ignore_schedule_allowed"] is True


async def test_calling_rules_carry_the_legal_floor_no_setting_can_widen(wired):
    floor = (await _rules(wired, None))["legal_floor"]
    assert floor["start"] == "08:00" and floor["end"] == "21:00"
    assert floor["days"] == [0, 1, 2, 3, 4, 5, 6] and floor["clock"] == "callee_local"
    assert floor["applies_with_call_anytime"] is True
    assert floor["australia"] == {"start": "09:00", "end": "20:00", "days": [0, 1, 2, 3, 4],
                                  "saturday_end": "17:00", "sunday": False}


async def test_calling_rules_read_is_tenant_scoped_and_failure_is_503(wired):
    await _rules(wired, None)
    query, args = wired.db.fetchval_calls[0]
    assert "tenant" in query.lower() or "id = $1" in query
    assert args == (TENANT,)
    wired.db = SimpleNamespace(fetchval=AsyncMock(side_effect=RuntimeError("down")))
    with pytest.raises(HTTPException) as caught:
        await ep.get_calling_rules(
            current_user=SimpleNamespace(id="u1", tenant_id=TENANT),
            db_client=SimpleNamespace(pool=object()))
    assert caught.value.status_code == 503


def _first_route(path: str, method: str = "GET"):
    scope = {"type": "http", "method": method, "path": path, "root_path": ""}
    for route in ep.router.routes:
        if route.matches(scope)[0] == Match.FULL:
            return route
    return None


def test_calling_rules_is_not_captured_by_the_campaign_id_route():
    route = _first_route("/campaigns/calling-rules")
    assert isinstance(route, APIRoute) and route.endpoint is ep.get_calling_rules
    assert _first_route(f"/campaigns/{CAMPAIGN}").endpoint is ep.get_campaign


def test_calling_rules_needs_the_campaign_read_permission():
    route = _first_route("/campaigns/calling-rules")
    names = {getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies}
    assert any(n.startswith("require_permission") for n in names), names


async def test_calling_rules_needs_a_tenant(wired):
    with pytest.raises(HTTPException) as caught:
        await ep.get_calling_rules(
            current_user=SimpleNamespace(id="u1", tenant_id=None),
            db_client=SimpleNamespace(pool=object()))
    assert caught.value.status_code == 400


# ---------------------------------------------------------------------------
# D3-13: first_speaker accepted at create and update, same validation as Start
# ---------------------------------------------------------------------------
_CREATE = {"name": "n", "voice_id": "voice-1", "persona_type": "receptionist",
           "agent_names": ["Sarah"], "company_name": "Dojo"}


def _error(model, **extra):
    with pytest.raises(ValidationError) as caught:
        model(**extra)
    return [(e["loc"], e["msg"], e["type"]) for e in caught.value.errors()]


@pytest.mark.parametrize("model,base", [
    (CampaignCreateRequest, _CREATE),
    (CampaignUpdateRequest, {k: v for k, v in _CREATE.items() if k != "voice_id"}),
])
def test_first_speaker_validation_is_the_same_as_starts(model, base):
    assert model(**base).first_speaker is None
    assert model(**base, first_speaker="user").first_speaker == "user"
    assert model(**base, first_speaker="agent").first_speaker == "agent"
    assert _error(model, **base, first_speaker="robot") == _error(
        CampaignStartRequest, first_speaker="robot")


async def _create(monkeypatch, **extra):
    inserted: list[dict] = []

    class _Insert:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            inserted.append(self.payload)
            return SimpleNamespace(error=None, data=[{"id": "c-1", **self.payload}])

    @asynccontextmanager
    async def _acquire(pool, tenant):
        yield object()

    monkeypatch.setattr(ep, "acquire_with_tenant", _acquire)
    monkeypatch.setattr("app.api.v1.endpoints.ai_options._fetch_tenant_config",
                        AsyncMock(return_value=None))
    monkeypatch.setattr("app.api.v1.endpoints.campaign_voice_config.build_campaign_voice_config",
                        AsyncMock(return_value=({"persona_type": "lead_gen"}, "voice-1")))
    db = SimpleNamespace(pool=object(), table=lambda name: SimpleNamespace(insert=_Insert))
    out = await ep.create_campaign(
        CampaignCreateRequest(**_CREATE, **extra), request=SimpleNamespace(),
        current_user=SimpleNamespace(tenant_id=TENANT, id="u1"), idempotency_key=None,
        db_client=db)
    return inserted[0], out


async def test_create_stores_first_speaker_in_calling_config(monkeypatch):
    payload, out = await _create(monkeypatch, first_speaker="user")
    assert payload["calling_config"] == {"first_speaker": "user"}
    assert out["campaign"]["calling_config"]["first_speaker"] == "user"


async def test_create_with_a_schedule_keeps_both(monkeypatch):
    payload, _ = await _create(
        monkeypatch, first_speaker="agent", calling_schedule={"timezone": "UTC"})
    assert payload["calling_config"] == {
        "ignore_schedule": False, "timezone": "UTC", "first_speaker": "agent"}


async def test_create_without_first_speaker_writes_no_key(monkeypatch):
    payload, _ = await _create(monkeypatch)
    assert "calling_config" not in payload
    payload, _ = await _create(monkeypatch, calling_schedule={"timezone": "UTC"})
    assert "first_speaker" not in payload["calling_config"]


async def test_update_sets_first_speaker_and_keeps_every_other_key(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row(batch_size=5, call_gap_seconds=30, trunk={"id": "t"}, first_speaker="agent",
                        timezone="UTC", ignore_schedule=False)
    db = _FakeDB([row])
    await ep.update_campaign(CAMPAIGN, _update_body(first_speaker="user"), SimpleNamespace(),
                             SimpleNamespace(id="u", tenant_id=TENANT, role="tenant_admin"), db)
    assert row["calling_config"] == {
        "batch_size": 5, "call_gap_seconds": 30, "trunk": {"id": "t"},
        "first_speaker": "user", "timezone": "UTC", "ignore_schedule": False}


async def test_update_with_schedule_and_first_speaker_applies_both(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row(batch_size=5, timezone="UTC", first_speaker="agent")
    db = _FakeDB([row])
    await ep.update_campaign(
        CAMPAIGN, _update_body(first_speaker="user", calling_schedule={"timezone": "Europe/London"}),
        SimpleNamespace(), SimpleNamespace(id="u", tenant_id=TENANT, role="tenant_admin"), db)
    assert row["calling_config"]["first_speaker"] == "user"
    assert row["calling_config"]["timezone"] == "Europe/London"
    assert row["calling_config"]["batch_size"] == 5


async def test_update_without_first_speaker_leaves_the_saved_choice(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row(first_speaker="user", batch_size=5)
    db = _FakeDB([row])
    await ep.update_campaign(CAMPAIGN, _update_body(), SimpleNamespace(),
                             SimpleNamespace(id="u", tenant_id=TENANT, role="tenant_admin"), db)
    assert row["calling_config"] == {"first_speaker": "user", "batch_size": 5}


async def test_update_first_speaker_is_written_for_a_json_text_config(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row()
    row["calling_config"] = json.dumps({"batch_size": 2})
    await ep.update_campaign(CAMPAIGN, _update_body(first_speaker="agent"), SimpleNamespace(),
                             SimpleNamespace(id="u", tenant_id=TENANT, role="tenant_admin"),
                             _FakeDB([row]))
    assert row["calling_config"] == {"batch_size": 2, "first_speaker": "agent"}


# ---------------------------------------------------------------------------
# Start reuses the stored choice; and a completed campaign starts like a paused one
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["paused", "completed"])
async def test_start_dispatches_the_choice_stored_at_create_for_paused_and_completed(monkeypatch, status):
    from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue

    db, queue = _DB(), _Queue([True])
    campaign = db.tables["campaigns"][0]
    campaign["status"] = status
    campaign["calling_config"] = {"first_speaker": "user"}
    service = svc.CampaignService(db, queue_service=queue)
    monkeypatch.setattr(service, "_evaluate_outbound_readiness",
                        AsyncMock(return_value={"ready": True}))
    result = await service.start_campaign("campaign-1", tenant_id="tenant-1")
    assert result.jobs_enqueued == 1
    assert queue.enqueued[0].first_speaker == "user"
    assert campaign["status"] == "running"


# ---------------------------------------------------------------------------
# D5-26: a finished campaign becomes ``completed``
# ---------------------------------------------------------------------------
import asyncio  # noqa: E402

from app.domain.services.dialer.job_states import ACTIVE_STATUSES, TERMINAL_STATUSES  # noqa: E402


class _CompletionConn:
    """Evaluates the writer's SQL against in-memory rows.

    A condition only counts when its text is in the statement, so dropping a
    guard from the SQL fails the test that needs it. The SQL itself is not
    executed by Postgres here.
    """

    def __init__(self, status="running", direction="outbound", tenant=TENANT):
        self.campaign = {"id": CAMPAIGN, "tenant_id": tenant, "status": status, "direction": direction}
        self.jobs, self.calls, self.leads, self.actions = [], [], [], []
        self.sql: list[tuple[str, tuple]] = []

    def _rows(self, rows, segment, args):
        return [r for r in rows if r["campaign_id"] == args[0]
                and ("tenant_id = $2" not in segment or r["tenant_id"] == args[1])]

    async def fetchval(self, query, *args):
        sql = " ".join(query.split())
        self.sql.append((sql, args))
        assert sql.startswith("UPDATE campaigns SET status = 'completed'")
        head, *segments = sql.split("NOT EXISTS")
        c = self.campaign
        if c["id"] != args[0] or c["tenant_id"] != args[1]:
            return None
        if "status = 'running'" in head and c["status"] != "running":
            return None
        if "direction = 'outbound'" in head and c["direction"] != "outbound":
            return None
        for seg in segments:
            if "FROM dialer_jobs" in seg and any(
                    r["status"] in args[2] for r in self._rows(self.jobs, seg, args)):
                return None
            if "FROM calls" in seg and any(
                    r["status"] in args[3] for r in self._rows(self.calls, seg, args)):
                return None
            if "FROM leads" in seg and any(
                    r["status"] in ("pending", "calling") for r in self._rows(self.leads, seg, args)):
                return None
            if "FROM assistant_actions" in seg and any(
                    r["type"] == "schedule_callback" and r["triggered_by"] == "voice"
                    and r["status"] == "scheduled" for r in self._rows(self.actions, seg, args)):
                return None
        c["status"] = "completed"
        return c["id"]


def _row(status, tenant=TENANT, **extra):
    return {"campaign_id": CAMPAIGN, "tenant_id": tenant, "status": status, **extra}


async def _finish(conn, campaign=CAMPAIGN, tenant=TENANT):
    return await svc.complete_campaign_if_finished(conn, campaign, tenant)


async def test_nothing_left_completes_a_running_campaign():
    conn = _CompletionConn()
    conn.jobs = [_row("completed"), _row("goal_achieved"), _row("failed")]
    conn.calls = [_row("completed")]
    conn.leads = [_row("called"), _row("completed"), _row("dnc")]
    assert await _finish(conn) is True
    assert conn.campaign["status"] == "completed"


@pytest.mark.parametrize("status", ACTIVE_STATUSES)
async def test_an_active_job_keeps_it_running(status):
    """pending/queued = waiting for its window or a callback just released; retry_scheduled = a retry due."""
    conn = _CompletionConn()
    conn.jobs = [_row(status)]
    assert await _finish(conn) is False and conn.campaign["status"] == "running"


@pytest.mark.parametrize("status", TERMINAL_STATUSES)
async def test_terminal_jobs_do_not_block(status):
    conn = _CompletionConn()
    conn.jobs = [_row(status)]
    assert await _finish(conn) is True


@pytest.mark.parametrize("status", CAMPAIGN_LIVE_CALL_STATUSES)
async def test_a_live_call_keeps_it_running(status):
    conn = _CompletionConn()
    conn.calls = [_row(status)]
    assert await _finish(conn) is False


@pytest.mark.parametrize("status", ["pending", "calling"])
async def test_a_pending_or_calling_contact_keeps_it_running(status):
    """Also covers contacts withheld by the permission check and newly added contacts: they stay pending."""
    conn = _CompletionConn()
    conn.leads = [_row(status)]
    assert await _finish(conn) is False


async def test_a_scheduled_callback_still_due_keeps_it_running():
    conn = _CompletionConn()
    conn.actions = [_row("scheduled", type="schedule_callback", triggered_by="voice")]
    assert await _finish(conn) is False
    conn.actions = [_row("completed", type="schedule_callback", triggered_by="voice"),
                    _row("failed", type="schedule_callback", triggered_by="voice")]
    assert await _finish(conn) is True


@pytest.mark.parametrize("status", ["paused", "stopped", "draft", "completed", "deleted"])
async def test_only_a_running_campaign_changes(status):
    """A pause or stop that landed first is never overwritten."""
    conn = _CompletionConn(status=status)
    assert await _finish(conn) is False and conn.campaign["status"] == status


async def test_an_inbound_campaign_never_completes():
    conn = _CompletionConn(direction="inbound")
    assert await _finish(conn) is False and conn.campaign["status"] == "running"


async def test_another_tenants_campaign_never_matches_and_their_rows_do_not_block():
    conn = _CompletionConn(tenant=OTHER_TENANT)
    assert await _finish(conn) is False and conn.campaign["status"] == "running"
    mine = _CompletionConn()
    mine.jobs = [_row("pending", tenant=OTHER_TENANT)]
    mine.calls = [_row("in_call", tenant=OTHER_TENANT)]
    assert await _finish(mine) is True


async def test_the_tenant_is_bound_on_the_campaign_and_every_table_in_the_condition():
    conn = _CompletionConn()
    await _finish(conn)
    sql, args = conn.sql[0]
    head, *segments = sql.split("NOT EXISTS")
    assert "tenant_id = $2" in head and args[:2] == (CAMPAIGN, TENANT)
    assert len(segments) == 4
    for table in ("dialer_jobs", "calls", "leads", "assistant_actions"):
        (segment,) = [s for s in segments if f"FROM {table}" in s]
        assert "tenant_id = $2" in segment and "campaign_id = $1" in segment
    assert list(args[2]) == list(ACTIVE_STATUSES)
    assert list(args[3]) == list(CAMPAIGN_LIVE_CALL_STATUSES)


async def test_it_logs_one_short_info_line_only_when_it_changed_the_row(caplog):
    conn = _CompletionConn()
    conn.leads = [_row("pending")]
    with caplog.at_level("INFO", logger=svc.logger.name):
        await _finish(conn)
        assert "campaign_completed" not in caplog.text
        conn.leads = []
        await _finish(conn)
    lines = [r.getMessage() for r in caplog.records if "campaign_completed" in r.getMessage()]
    assert len(lines) == 1 and CAMPAIGN[:8] in lines[0] and CAMPAIGN not in lines[0]


async def test_two_last_calls_ending_together_complete_it_exactly_once():
    """The counters UPDATE row-locks the campaign, so the two settlements run one after the other."""
    conn = _CompletionConn()
    conn.calls = [_row("in_call", id="A"), _row("in_call", id="B")]
    row_lock = asyncio.Lock()
    results = {}

    async def settle(call_id):
        async with row_lock:
            next(c for c in conn.calls if c["id"] == call_id)["status"] = "completed"
            await asyncio.sleep(0)
            results[call_id] = await _finish(conn)

    await asyncio.gather(settle("A"), settle("B"))
    assert sorted(results.values()) == [False, True]
    assert conn.campaign["status"] == "completed"


async def test_a_completed_campaign_started_with_new_contacts_dispatches_like_a_paused_one(monkeypatch):
    from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue

    db, queue = _DB(lead_count=3), _Queue([True, True, True])
    db.tables["campaigns"][0]["status"] = "completed"
    service = svc.CampaignService(db, queue_service=queue)
    monkeypatch.setattr(service, "_evaluate_outbound_readiness",
                        AsyncMock(return_value={"ready": True}))
    result = await service.start_campaign("campaign-1", tenant_id="tenant-1")
    assert result.jobs_enqueued == 3 and db.tables["campaigns"][0]["status"] == "running"


async def test_call_this_list_on_a_completed_campaign_writes_running_and_dispatches(monkeypatch):
    from tests.unit.test_campaign_start_dispatch_truth import _DB, _Queue

    db, queue = _DB(lead_count=2), _Queue([True, True])
    db.tables["campaigns"][0]["status"] = "completed"
    service = svc.CampaignService(db, queue_service=queue)
    monkeypatch.setattr(service, "_evaluate_outbound_readiness",
                        AsyncMock(return_value={"ready": True}))
    result = await service.start_campaign("campaign-1", tenant_id="tenant-1", allow_running=True)
    assert result.jobs_enqueued == 2 and db.tables["campaigns"][0]["status"] == "running"


def test_the_documented_writer_of_completed_names_the_call_end():
    from app.domain.models.campaign import CAMPAIGN_STATUS_WRITERS

    text = CAMPAIGN_STATUS_WRITERS["completed"]
    assert "no writer" not in text and "last call" in text and "Start" in text


# --- settlement: the writer is called after the counters, and can never break settlement ---
from tests.unit.test_call_service_pooled_teardown import (  # noqa: E402
    _FakeConn as _SettleConn, _rls_bypass_context, _service as _settle_service,
)
from app.domain.models.dialer_job import CallOutcome  # noqa: E402


def _settle_conn(outcome_job_status="processing"):
    return _SettleConn(
        calls_row={"id": "call-1", "lead_id": "lead-1", "campaign_id": "camp-1",
                   "dialer_job_id": "job-1", "status": "initiated"},
        leads_row={"id": "lead-1", "call_attempts": 0, "status": "calling"},
        dialer_jobs_row={"id": "job-1", "status": outcome_job_status, "attempt_number": 1,
                         "tenant_id": "tenant-1", "priority": 5, "phone_number": "+15550002222"},
    )


async def test_settlement_calls_the_writer_after_the_counters_with_the_calls_tenant(monkeypatch):
    conn = _settle_conn()
    seen = []

    async def spy(c, campaign_id, tenant_id):
        seen.append((c is conn, campaign_id, tenant_id, conn.campaigns_row["calls_completed"]))
        return False

    monkeypatch.setattr(svc, "complete_campaign_if_finished", spy)
    await _settle_service(conn)._handle_call_status_pooled(
        "call-1", CallOutcome.ANSWERED, "answered", duration=5)
    assert seen == [(True, "camp-1", conn.calls_row["tenant_id"], 1)]


async def test_a_lead_awaiting_a_retry_does_not_reach_the_writer(monkeypatch):
    conn = _settle_conn()
    spy = AsyncMock(return_value=False)
    monkeypatch.setattr(svc, "complete_campaign_if_finished", spy)
    await _settle_service(conn)._handle_call_status_pooled(
        "call-1", CallOutcome.BUSY, "busy", duration=0)
    spy.assert_not_called()


async def test_a_failing_writer_never_breaks_call_settlement(monkeypatch, caplog):
    conn = _settle_conn()
    monkeypatch.setattr(svc, "complete_campaign_if_finished",
                        AsyncMock(side_effect=RuntimeError("synthetic failure")))
    with caplog.at_level("WARNING"):
        job_id, campaign_id, retry = await _settle_service(conn)._handle_call_status_pooled(
            "call-1", CallOutcome.ANSWERED, "answered", duration=5)
    assert (job_id, campaign_id, retry) == ("job-1", "camp-1", None)
    assert conn.calls_row["terminal_settled_at"] == "NOW"
    assert conn.leads_row["status"] == "contacted"
    assert conn.dialer_jobs_row["status"] == "completed"
    assert conn.campaigns_row["calls_completed"] == 1
    assert "campaign_completion_check_failed" in caplog.text


# ---------------------------------------------------------------------------
# D5-26 follow-up: a job the dialer ends WITHOUT a call also runs the check
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from app.domain.models.dialer_job import DialerJob, JobStatus  # noqa: E402
from app.workers.dialer_worker import DialerWorker  # noqa: E402


class _JobConn:
    def __init__(self, rows=1):
        self.rows = rows
        self.events: list[str] = []
        self.statements: list[tuple[str, tuple]] = []
        self.savepoints = 0

    async def fetch(self, query, *args):
        self.events.append("job_update")
        return [{"id": "j"}] * self.rows

    async def execute(self, query, *args):
        self.events.append("lock")
        self.statements.append((" ".join(query.split()), args))

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                conn.savepoints += 1
                conn.events.append("savepoint")

            async def __aexit__(self, *exc):
                return False

        return _Tx()


def _worker(conn):
    worker = object.__new__(DialerWorker)

    @asynccontextmanager
    async def acquire():
        yield conn

    worker._acquire_db = acquire
    return worker


def _job():
    return DialerJob(job_id=_cid(70), tenant_id=TENANT, campaign_id=CAMPAIGN,
                     lead_id=_cid(71), phone_number="+14155550100")


@pytest.fixture
def writer(monkeypatch):
    calls = []

    async def spy(conn, campaign_id, tenant_id):
        conn.events.append("check")
        calls.append((conn, campaign_id, tenant_id))
        return False

    monkeypatch.setattr(svc, "complete_campaign_if_finished", spy)
    return calls


@pytest.mark.parametrize("status", [JobStatus.FAILED, JobStatus.SKIPPED, JobStatus.BLOCKED,
                                    JobStatus.NON_RETRYABLE, JobStatus.COMPLETED])
async def test_a_terminal_job_status_written_without_a_call_runs_the_check(writer, status):
    conn = _JobConn()
    await _worker(conn)._update_job_status(_job(), status, error="e")
    assert writer == [(conn, CAMPAIGN, TENANT)]
    # the check sees the job update (same connection, after it), inside a savepoint
    assert conn.events.index("job_update") < conn.events.index("savepoint") < conn.events.index("check")
    assert conn.savepoints == 1


async def test_the_check_row_locks_the_campaign_first_so_two_endings_serialise(writer):
    conn = _JobConn()
    await _worker(conn)._update_job_status(_job(), JobStatus.FAILED, error="e")
    (sql, args), = conn.statements
    assert "FOR UPDATE" in sql and "tenant_id = $2" in sql and args == (CAMPAIGN, TENANT)
    assert conn.events.index("lock") < conn.events.index("check")


@pytest.mark.parametrize("status", [JobStatus.RETRY_SCHEDULED, JobStatus.PENDING, JobStatus.PROCESSING])
async def test_an_active_status_does_not_run_the_check(writer, status):
    await _worker(_JobConn())._update_job_status(_job(), status)
    assert writer == []


async def test_a_deferral_with_scheduled_at_does_not_run_the_check(writer):
    due = datetime.now(timezone.utc) + timedelta(minutes=5)
    await _worker(_JobConn())._update_job_status(
        _job(), JobStatus.RETRY_SCHEDULED, reason="r", scheduled_at=due)
    assert writer == []


async def test_a_status_written_with_a_call_id_does_not_run_the_check(writer):
    """A call exists, so settlement does the check."""
    await _worker(_JobConn())._update_job_status(_job(), JobStatus.COMPLETED, call_id="call-1")
    assert writer == []


async def test_a_failed_ownership_update_raises_and_never_reaches_the_check(writer):
    with pytest.raises(RuntimeError):
        await _worker(_JobConn(rows=0))._update_job_status(_job(), JobStatus.FAILED, error="e")
    assert writer == []


async def test_a_failing_check_never_fails_or_undoes_the_job_update(monkeypatch, caplog):
    monkeypatch.setattr(svc, "complete_campaign_if_finished",
                        AsyncMock(side_effect=RuntimeError("synthetic failure")))
    conn = _JobConn()
    with caplog.at_level("WARNING"):
        await _worker(conn)._update_job_status(_job(), JobStatus.FAILED, error="e")
    assert "job_update" in conn.events and conn.savepoints == 1
    assert "campaign_completion_check_failed" in caplog.text
