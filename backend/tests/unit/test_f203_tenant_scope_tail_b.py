"""F2-03 (tail B: call-path and worker files): statements name their tenant.

Production has run with an application role that bypasses row-level security
and the dialer's own connections still do, so a statement that does not name its
tenant is not isolated at all. Each test runs the real function on a connection
that records ``(sql, args)`` and checks two things about the statement:

* the tenant predicate is in the SQL and is bound to the tenant the caller
  already holds (the authenticated tenant, the job's tenant, or the tenant of the
  row the function has just read), not to a constant and not to nothing;
* where the SQL is portable it is also executed on SQLite beside a second
  tenant's rows: another tenant's row matches nothing, the caller's own matches.

Section 1 - statements that lacked a predicate and now carry one. Every test
passes the same call twice, once per tenant, so a hard-coded value cannot satisfy it.

Section 2 - claims the baseline exceptions for this slice rest on. These
statements already bind the tenant in a form the scanner cannot prove (CTE,
EXISTS, ON CONFLICT, a dynamic fragment, ``call_legs`` / ``call_events`` having no
``tenant_id`` column). They pass before and after the change; they exist so a
later edit that drops the binding fails here, not only in the ratchet.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

TENANT_A = "a1111111-1111-4111-8111-111111111111"
TENANT_B = "b2222222-2222-4222-8222-222222222222"
TENANTS = [TENANT_A, TENANT_B]
IDS = ["tenant-a", "tenant-b"]
OTHER = {TENANT_A: TENANT_B, TENANT_B: TENANT_A}
CALL = "c3333333-3333-4333-8333-333333333333"
FOREIGN_CALL = "c4444444-3333-4333-8333-333333333333"
CAMPAIGN = "d4444444-4444-4444-8444-444444444444"
LEAD = "e5555555-5555-4555-8555-555555555555"
FEEDBACK = "f6666666-6666-4666-8666-666666666666"
FOREIGN_FEEDBACK = "f7777777-6666-4666-8666-666666666666"
USER = "a8888888-8888-4888-8888-888888888888"
TRUNK = "b9999999-9999-4999-8999-999999999999"
REMINDER = "c0000000-0000-4000-8000-000000000001"
FOREIGN_REMINDER = "c0000000-0000-4000-8000-000000000002"


# -- helpers ------------------------------------------------------------------------------


def flat(sql):
    return " ".join(sql.split())


def bound_tenants(sql, args):
    """Every value the statement compares a ``tenant_id`` column with, read from its bind slots."""
    return [args[int(slot) - 1] for slot in re.findall(r"\btenant_id\s*=\s*\$(\d+)", sql, re.I)]


def only(calls, marker):
    """The one recorded ``(sql, args)`` whose flattened text contains ``marker``."""
    hits = [(flat(sql), args) for sql, args in calls if marker in flat(sql)]
    assert len(hits) == 1, f"expected exactly one statement containing {marker!r}, got {len(hits)}"
    return hits[0]


def insert_binding(sql, column, args):
    """The value an ``INSERT INTO t (cols) VALUES (slots)`` binds to ``column``."""
    columns, values = re.search(r"\(([^)]*)\)\s*VALUES\s*\((.*?)\)\s*(?:ON|RETURNING|$)", sql, re.I | re.S).groups()
    names = [name.strip() for name in columns.split(",")]
    slots = [slot.strip() for slot in re.split(r",\s*(?![^()]*\))", values)]
    return args[int(re.match(r"\$(\d+)", slots[names.index(column)]).group(1)) - 1]


def sqlite_sql(sql, *, keep_returning=False):
    """The few Postgres spellings these statements use, in SQLite's."""
    sql = flat(sql)
    if not keep_returning:
        sql = re.sub(r"\s+RETURNING\b.*$", "", sql, flags=re.I)
    sql = re.sub(r"\s+FOR UPDATE\s*$", "", sql, flags=re.I)
    sql = re.sub(r"::\s*(?:uuid|text|jsonb)\b(?:\[\])?", "", sql, flags=re.I)
    sql = re.sub(r"\$(\d+)", r"?\1", sql)
    return sql.replace("NOW()", "CURRENT_TIMESTAMP")


def text_args(args):
    return tuple(None if value is None else str(value) for value in args)


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class Recorder:
    """Fake asyncpg connection: records ``(sql, args)``; answers by the first matching marker."""

    def __init__(self, replies=(), *, execute_result="UPDATE 1"):
        self.replies = list(replies)
        self.calls: list[tuple[str, tuple]] = []
        self.execute_result = execute_result

    def _answer(self, sql, default):
        for marker, value in self.replies:
            if marker in flat(sql):
                return value
        return default

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self._answer(sql, None)

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._answer(sql, [])

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self._answer(sql, None)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return self._answer(sql, self.execute_result)

    def transaction(self):
        return Transaction()


def acquirer(conn, seen):
    """A stand-in for ``acquire_with_tenant`` that yields ``conn`` and remembers the tenant it was given."""

    @asynccontextmanager
    async def acquire(_pool, tenant=None, **_kwargs):
        seen.append(tenant)
        yield conn

    return acquire


# -- lead_details.set_lead_fields: the replace-all DELETE -----------------------------------


async def run_set_lead_fields(monkeypatch, tenant):
    from app.api.v1.endpoints import lead_details

    conn = Recorder([("FROM campaigns", {"system_prompt": "", "script_config": {}})])
    seen: list = []
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquirer(conn, seen))
    monkeypatch.setattr(lead_details, "get_container", lambda: SimpleNamespace(is_initialized=True, db_pool=object()))
    monkeypatch.setattr(lead_details, "_service", lambda: SimpleNamespace(fields_for_campaign=AsyncMock(return_value=[])))
    await lead_details.set_lead_fields(CAMPAIGN, [], current_user=SimpleNamespace(tenant_id=tenant))
    assert seen == [tenant]
    return conn


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_lead_field_replace_all_delete_names_the_callers_tenant(monkeypatch, tenant):
    conn = await run_set_lead_fields(monkeypatch, tenant)
    sql, args = only(conn.calls, "DELETE FROM campaign_lead_fields")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the caller's tenant: {sql} {args}"
    assert CAMPAIGN in args


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_lead_field_replace_all_delete_cannot_touch_another_tenants_rows(monkeypatch, tenant):
    conn = await run_set_lead_fields(monkeypatch, tenant)
    sql, args = only(conn.calls, "DELETE FROM campaign_lead_fields")
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE campaign_lead_fields (tenant_id TEXT, campaign_id TEXT, field_key TEXT);")
    # The caller's own definition and, under the same campaign id, a row another tenant holds
    # (legacy inconsistent data): only the caller's own may go.
    db.execute("INSERT INTO campaign_lead_fields VALUES (?, ?, 'own')", (tenant, CAMPAIGN))
    db.execute("INSERT INTO campaign_lead_fields VALUES (?, ?, 'foreign')", (OTHER[tenant], CAMPAIGN))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 1
    assert db.execute("SELECT tenant_id, field_key FROM campaign_lead_fields").fetchall() == [(OTHER[tenant], "foreign")]


# -- call_feedback_service: replace and the first transcription claim -------------------------


def stored_audio():
    from app.domain.services.call_feedback_service import StoredFeedbackAudio

    return StoredFeedbackAudio(provider="s3", bucket="bucket", key="key")


async def run_replace(monkeypatch, tenant):
    from app.domain.services import call_feedback_service as service

    conn = Recorder([
        ("DELETE FROM call_feedback", {"audio_storage_provider": "s3", "audio_bucket": "b", "audio_key": "k"}),
        ("INSERT INTO call_feedback", {"id": FEEDBACK}),
    ])
    monkeypatch.setattr(service, "acquire_with_tenant", acquirer(conn, []))
    swapped = await service.CallFeedbackRepository(object()).replace(
        previous_id=FOREIGN_FEEDBACK, feedback_id=FEEDBACK, tenant_id=tenant, call_id=CALL, created_by=USER,
        stored=stored_audio(), mime_type="audio/webm", audio_size_bytes=10, audio_sha256="0" * 64,
        duration_seconds=1.0,
    )
    assert swapped is not None
    return conn


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_feedback_replace_delete_names_the_callers_tenant(monkeypatch, tenant):
    conn = await run_replace(monkeypatch, tenant)
    sql, args = only(conn.calls, "DELETE FROM call_feedback")
    assert bound_tenants(sql, args) == [UUID(tenant)], f"no tenant predicate bound to the caller's tenant: {sql} {args}"
    assert args[:2] == (UUID(CALL), UUID(FOREIGN_FEEDBACK))


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_feedback_replace_cannot_delete_another_tenants_note(monkeypatch, tenant):
    conn = await run_replace(monkeypatch, tenant)
    sql, args = only(conn.calls, "DELETE FROM call_feedback")
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE call_feedback (id TEXT, tenant_id TEXT, call_id TEXT);")
    # Another tenant's note is the one this id names: the swap must find nothing to remove.
    db.execute("INSERT INTO call_feedback VALUES (?, ?, ?)", (FOREIGN_FEEDBACK, OTHER[tenant], CALL))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 0
    assert db.execute("SELECT id FROM call_feedback").fetchall() == [(FOREIGN_FEEDBACK,)]


async def run_first_attempt(monkeypatch, tenant):
    from app.domain.services import call_feedback_service as service

    conn = Recorder([("UPDATE call_feedback", {"id": FEEDBACK})])
    monkeypatch.setattr(service, "acquire_with_tenant", acquirer(conn, []))
    claimed = await service.CallFeedbackRepository(object()).begin_initial_attempt(tenant, FEEDBACK)
    assert claimed == {"id": FEEDBACK}
    return conn


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_feedback_first_transcription_claim_names_the_callers_tenant(monkeypatch, tenant):
    conn = await run_first_attempt(monkeypatch, tenant)
    sql, args = only(conn.calls, "UPDATE call_feedback")
    assert bound_tenants(sql, args) == [UUID(tenant)], f"no tenant predicate bound to the caller's tenant: {sql} {args}"
    assert args[0] == UUID(FEEDBACK)


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_feedback_first_transcription_claim_cannot_claim_another_tenants_note(monkeypatch, tenant):
    conn = await run_first_attempt(monkeypatch, tenant)
    sql, args = only(conn.calls, "UPDATE call_feedback")
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE call_feedback (id TEXT, tenant_id TEXT, transcript_status TEXT, transcription_attempts INT,"
        " transcription_started_at TEXT, transcript_error TEXT, updated_at TEXT);"
    )
    db.execute("INSERT INTO call_feedback VALUES (?, ?, 'pending', 0, NULL, NULL, NULL)", (FEEDBACK, OTHER[tenant]))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 0
    assert db.execute("SELECT transcription_attempts FROM call_feedback").fetchall() == [(0,)]
    db.execute("UPDATE call_feedback SET tenant_id = ?", (tenant,))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 1


# -- call_status.record_call_state: the compare-and-set and the visibility probe ----------------


async def run_record_state(monkeypatch, tenant, state, *, update_result="UPDATE 1"):
    from app.domain.services import call_status, event_emitter

    conn = Recorder(execute_result=update_result)
    seen: list = []
    monkeypatch.setattr(call_status, "acquire_with_tenant", acquirer(conn, seen))
    monkeypatch.setattr(event_emitter, "emit_event_via_pool", AsyncMock())
    await call_status.record_call_state(object(), call_id=CALL, tenant_id=tenant, new_state=state)
    assert seen == [tenant]
    return conn


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
@pytest.mark.parametrize("state", ["answered", "ringing"], ids=["with-timestamp", "plain"])
async def test_call_state_update_names_the_calls_tenant(monkeypatch, tenant, state):
    conn = await run_record_state(monkeypatch, tenant, state)
    sql, args = only(conn.calls, "UPDATE calls SET status = $1")
    # "answered" writes answered_at (the timestamp branch); "ringing" has no timestamp column (the plain branch).
    assert ("answered_at" in sql) is (state == "answered")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the call's tenant: {sql} {args}"
    assert args[:2] == (state, CALL)
    assert "status IS DISTINCT FROM $1" in sql, "the monotonic compare-and-set guard must survive"


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_call_state_visibility_probe_names_the_calls_tenant(monkeypatch, tenant):
    conn = await run_record_state(monkeypatch, tenant, "answered", update_result="UPDATE 0")
    sql, args = only(conn.calls, "SELECT 1 FROM calls WHERE id = $1")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the call's tenant: {sql} {args}"
    assert args[0] == CALL


# -- lead_capture_service.missing_required: the field definitions join ------------------------


class SqliteFetchConn:
    """Runs the SELECT it is given on SQLite and returns the rows as dicts."""

    def __init__(self, db):
        self.db = db
        self.db.row_factory = sqlite3.Row
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [dict(row) for row in self.db.execute(sqlite_sql(sql), text_args(args)).fetchall()]


def lead_field_db(tenant):
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE calls (id TEXT, tenant_id TEXT, campaign_id TEXT);"
        "CREATE TABLE campaign_lead_fields (tenant_id TEXT, campaign_id TEXT, field_key TEXT, field_type TEXT,"
        " is_required INT, sort_order INT);"
    )
    db.execute("INSERT INTO calls VALUES (?, ?, ?)", (CALL, tenant, CAMPAIGN))
    db.execute("INSERT INTO campaign_lead_fields VALUES (?, ?, 'email', 'email', 1, 1)", (tenant, CAMPAIGN))
    # Legacy inconsistent data: another tenant's field definition under this call's campaign id.
    db.execute("INSERT INTO campaign_lead_fields VALUES (?, ?, 'foreign_secret', 'text', 1, 2)", (OTHER[tenant], CAMPAIGN))
    return db


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_missing_required_reads_only_the_calls_own_tenants_field_definitions(monkeypatch, tenant):
    from app.domain.services.lead_capture_service import LeadCaptureService

    conn = SqliteFetchConn(lead_field_db(tenant))
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquirer(conn, []))
    service = LeadCaptureService(object())
    service.details_for_call = AsyncMock(return_value=[])
    assert await service.missing_required(tenant, CALL) == ["email"], (
        "another tenant's field definition must not become a required field of this call"
    )
    sql, args = only(conn.calls, "FROM calls c JOIN campaign_lead_fields f")
    assert bound_tenants(sql, args) == [tenant]
    assert re.search(r"f\.tenant_id\s*=\s*c\.tenant_id", sql), f"the field join does not tie f to the call's tenant: {sql}"


# -- termination_receipts.persist_termination_receipt: the call lock ----------------------------

BACKEND = "pbx1:" + "a" * 64
LEG_ROW = {"id": "leg-row-1", "provider": "asterisk", "provider_leg_id": "child-1", "status": "ended",
           "created_at": datetime.now(timezone.utc) - timedelta(minutes=2)}


def receipt_context(tenant):
    from app.domain.services.telephony.termination import TerminationContext

    return TerminationContext(
        call_id=CALL, tenant_id=tenant, provider_call_id="parent-1", previous_status="in_call",
        provider_leg_ids=("child-1",), provider="asterisk", provider_legs=(("child-1", "asterisk"),),
        control_backend=BACKEND, leg_inventory=(("leg-row-1", "asterisk", "child-1"),),
    )


class ReceiptConn:
    """``calls`` lives in SQLite so the lock SELECT really runs; legs and the event insert are recorded."""

    def __init__(self, call_tenant):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE calls (id TEXT, tenant_id TEXT, provider TEXT, provider_call_id TEXT,"
            " external_call_uuid TEXT, status TEXT, route_snapshot TEXT)"
        )
        self.db.execute(
            "INSERT INTO calls VALUES (?, ?, 'asterisk', 'parent-1', NULL, 'termination_pending', ?)",
            (CALL, call_tenant, json.dumps({"control_backend": BACKEND})),
        )
        self.calls: list[tuple[str, tuple]] = []
        self.locked: list[str] = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        row = self.db.execute(sqlite_sql(sql), text_args(args)).fetchone()
        if row is None:
            return None
        found = dict(row)
        self.locked.append(found["tenant_id"])
        found["route_snapshot"] = json.loads(found["route_snapshot"])
        return found

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return [dict(LEG_ROW)]

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "INSERT 0 1"


async def run_receipt(monkeypatch, *, call_tenant, context_tenant):
    from app.domain.services.telephony import termination_receipts

    conn = ReceiptConn(call_tenant)
    monkeypatch.setattr(termination_receipts, "acquire_with_tenant", acquirer(conn, []))
    result = await termination_receipts.persist_termination_receipt(
        object(), receipt_context(context_tenant), requested=True
    )
    return conn, result


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_termination_receipt_locks_the_call_of_the_contexts_tenant(monkeypatch, tenant):
    conn, result = await run_receipt(monkeypatch, call_tenant=tenant, context_tenant=tenant)
    sql, args = only(conn.calls, "FROM calls WHERE id=$1::uuid")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the context's tenant: {sql} {args}"
    assert result.saved is True and result.code == "saved", "a call of the context's own tenant must still be receipted"
    assert len([1 for call_sql, _ in conn.calls if "INSERT INTO call_events" in call_sql]) == 1


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_termination_receipt_never_locks_another_tenants_call(monkeypatch, tenant):
    conn, result = await run_receipt(monkeypatch, call_tenant=OTHER[tenant], context_tenant=tenant)
    assert conn.locked == [], "the lock SELECT returned (and locked FOR UPDATE) another tenant's call row"
    assert result.saved is False and result.code == "identity_changed"
    assert not [1 for call_sql, _ in conn.calls if "INSERT INTO call_events" in call_sql]


# -- transfer_restart_recovery.claim_inbound_transfer_takeovers: the fencing UPDATE -------------


class ClaimConn:
    def __init__(self, parents):
        self.parents = parents
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "FROM calls c" in sql:
            return [dict(parent) for parent in self.parents]
        if "FROM call_legs" in sql:
            return [{"id": "33333333-3333-3333-3333-333333333333", "provider_leg_id": "talky-xfer-0123456789abcdefabcd"}]
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return {"id": args[0]}


async def test_takeover_fence_names_each_parents_own_tenant(monkeypatch):
    from app.domain.services.telephony import transfer_restart_recovery as recovery

    parents = [
        {"id": CALL, "tenant_id": TENANT_A, "provider": "asterisk", "provider_call_id": "inbound-a", "status": "in_call"},
        {"id": FOREIGN_CALL, "tenant_id": TENANT_B, "provider": "asterisk", "provider_call_id": "inbound-b", "status": "in_call"},
    ]
    conn = ClaimConn(parents)
    monkeypatch.setattr(recovery, "acquire_with_tenant", acquirer(conn, []))
    claims = await recovery.claim_inbound_transfer_takeovers(object(), exclusive_owner_confirmed=True)
    assert [claim.tenant_id for claim in claims] == [TENANT_A, TENANT_B]
    updates = [(flat(sql), args) for sql, args in conn.calls if "UPDATE calls" in sql]
    assert len(updates) == 2
    owner = {CALL: TENANT_A, FOREIGN_CALL: TENANT_B}
    for sql, args in updates:
        assert bound_tenants(sql, args) == [owner[args[0]]], (
            f"the fence must name the tenant of the very parent it fences: {sql} {args}"
        )


# -- voice_orchestrator._persist_prompt_identity -------------------------------------------------


async def run_prompt_identity(monkeypatch, *, tenant_argument, config_tenant):
    from app.domain.services.voice_orchestrator import VoiceOrchestrator

    conn = Recorder()
    seen: list = []
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquirer(conn, seen))
    config = SimpleNamespace(prompt_hash="h" * 8, prompt_template="template", prompt_version="v1", tenant_id=config_tenant)
    orchestrator = VoiceOrchestrator(db_client=SimpleNamespace(pool=object()))
    await orchestrator._persist_prompt_identity(CALL, config, tenant_argument)
    return only(conn.calls, "UPDATE calls"), seen


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_prompt_identity_update_names_the_sessions_tenant(monkeypatch, tenant):
    (sql, args), seen = await run_prompt_identity(monkeypatch, tenant_argument=tenant, config_tenant=OTHER[tenant])
    assert seen == [tenant]
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the session's tenant: {sql} {args}"
    assert args[0] == CALL


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_prompt_identity_update_falls_back_to_the_configs_tenant(monkeypatch, tenant):
    (sql, args), seen = await run_prompt_identity(monkeypatch, tenant_argument=None, config_tenant=tenant)
    assert seen == [tenant]
    assert bound_tenants(sql, args) == [tenant]


async def test_prompt_identity_update_matches_by_call_id_only_for_a_session_with_no_tenant(monkeypatch):
    (sql, args), seen = await run_prompt_identity(monkeypatch, tenant_argument=None, config_tenant=None)
    assert seen == [None], "a session with no tenant keeps its platform-bypass connection"
    assert bound_tenants(sql, args) == [None]
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE calls (id TEXT, tenant_id TEXT, prompt_template TEXT, prompt_version TEXT, prompt_hash TEXT);"
    )
    db.execute("INSERT INTO calls VALUES (?, ?, NULL, NULL, NULL)", (CALL, TENANT_A))
    # No tenant known: the write still lands on the durable call it was bound to, as before.
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 1


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_prompt_identity_update_cannot_write_another_tenants_call(monkeypatch, tenant):
    (sql, args), _ = await run_prompt_identity(monkeypatch, tenant_argument=tenant, config_tenant=None)
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE calls (id TEXT, tenant_id TEXT, prompt_template TEXT, prompt_version TEXT, prompt_hash TEXT);"
    )
    db.execute("INSERT INTO calls VALUES (?, ?, NULL, NULL, NULL)", (CALL, OTHER[tenant]))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 0
    assert db.execute("SELECT prompt_hash FROM calls").fetchall() == [(None,)]
    db.execute("UPDATE calls SET tenant_id = ?", (tenant,))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 1


# -- seed_platform_sip_trunk.seed_for_tenant: the in-place credential update ---------------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_platform_trunk_credential_update_names_the_tenant(monkeypatch, tenant):
    from app.services.scripts import seed_platform_sip_trunk as seed

    monkeypatch.setenv("PLATFORM_SIP_DOMAIN", "sip.example.invalid")
    monkeypatch.setenv("PLATFORM_SIP_USERNAME", "user")
    monkeypatch.setenv("PLATFORM_SIP_PASSWORD", "secret")
    monkeypatch.setattr(
        "app.infrastructure.connectors.encryption.get_encryption_service",
        lambda: SimpleNamespace(encrypt=lambda _plain: "ciphertext"),
    )
    conn = Recorder([("FROM tenant_sip_trunks", {"id": TRUNK})])
    await seed.seed_for_tenant(conn, tenant, flip_active_provider=False)
    sql, args = only(conn.calls, "UPDATE tenant_sip_trunks")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the seeded tenant: {sql} {args}"
    assert TRUNK in args
    select_sql, select_args = only(conn.calls, "FROM tenant_sip_trunks WHERE")
    assert bound_tenants(select_sql, select_args) == [tenant]


# -- reminder_worker._process_reminder: claim, sent, retry and failed writes ---------------------


def reminder_row(tenant, *, retry_count=0):
    return {"id": REMINDER, "tenant_id": tenant, "lead_id": None, "meeting_id": None, "type": "email",
            "email": "person@example.invalid", "phone_number": None, "retry_count": retry_count, "max_retries": 3,
            "first_name": "Test", "last_name": "Contact"}


async def run_reminder(monkeypatch, tenant, outcome, *, retry_count=0):
    from app.workers import reminder_worker

    conn = Recorder()
    seen: list = []
    monkeypatch.setattr(reminder_worker, "acquire_with_tenant", acquirer(conn, seen))
    worker = reminder_worker.ReminderWorker()
    worker._db_pool = object()
    worker._send_email_reminder = AsyncMock(return_value=outcome)
    await worker._process_reminder(reminder_row(tenant, retry_count=retry_count))
    assert seen == [tenant]
    return conn


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_reminder_claim_and_sent_writes_name_the_reminders_tenant(monkeypatch, tenant):
    conn = await run_reminder(monkeypatch, tenant, {"success": True, "message_id": "m-1"})
    for marker in ("SET status = 'processing'", "SET status = 'sent'"):
        sql, args = only(conn.calls, marker)
        assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the reminder's tenant: {sql} {args}"
        assert REMINDER in args


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_reminder_retry_write_names_the_reminders_tenant(monkeypatch, tenant):
    conn = await run_reminder(monkeypatch, tenant, {"success": False, "error": "smtp 421"})
    sql, args = only(conn.calls, "SET status = 'pending'")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the reminder's tenant: {sql} {args}"
    assert REMINDER in args


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_reminder_final_failure_write_names_the_reminders_tenant(monkeypatch, tenant):
    conn = await run_reminder(monkeypatch, tenant, {"success": False, "error": "smtp 550"}, retry_count=2)
    sql, args = only(conn.calls, "SET status = 'failed'")
    assert bound_tenants(sql, args) == [tenant], f"no tenant predicate bound to the reminder's tenant: {sql} {args}"
    assert REMINDER in args


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_reminder_claim_cannot_claim_another_tenants_reminder(monkeypatch, tenant):
    conn = await run_reminder(monkeypatch, tenant, {"success": True, "message_id": "m-1"})
    sql, args = only(conn.calls, "SET status = 'processing'")
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE reminders (id TEXT, tenant_id TEXT, status TEXT, idempotency_key TEXT);")
    db.execute("INSERT INTO reminders VALUES (?, ?, 'pending', NULL)", (REMINDER, OTHER[tenant]))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 0
    assert db.execute("SELECT status FROM reminders").fetchall() == [("pending",)]
    db.execute("UPDATE reminders SET tenant_id = ?", (tenant,))
    assert db.execute(sqlite_sql(sql), text_args(args)).rowcount == 1


# =============================================================================================
# Section 2 - claims the baseline exceptions rest on (pass before and after the change)
# =============================================================================================


# -- call_redial_service: the original call context and the busy-contact probe ------------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
@pytest.mark.parametrize("lock", [False, True], ids=["read", "lock"])
async def test_redial_context_binds_the_callers_tenant_with_and_without_the_row_lock(tenant, lock):
    from app.domain.services import call_redial_service

    conn = Recorder([("FROM calls c", {"id": CALL})])
    await call_redial_service._context(conn, tenant, CALL, lock=lock)
    sql, args = only(conn.calls, "FROM calls c")
    assert bound_tenants(sql, args) == [tenant]
    assert sql.endswith("FOR UPDATE OF c,l") is lock, "the lock fragment is the only dynamic part"


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_redial_job_state_binds_the_callers_tenant_in_every_probe(tenant):
    from app.domain.services import call_redial_service

    conn = Recorder()
    await call_redial_service._job_state(conn, tenant, {"lead_id": LEAD}, FEEDBACK)
    own, args = only(conn.calls, "FROM dialer_jobs WHERE id=$1::uuid")
    assert bound_tenants(own, args) == [tenant]
    busy, args = only(conn.calls, "SELECT EXISTS(SELECT 1 FROM dialer_jobs")
    assert bound_tenants(busy, args) == [tenant, tenant], "both EXISTS arms (jobs and calls) must name the tenant"


# -- lead_capture_service.fields_for_campaign ------------------------------------------------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
@pytest.mark.parametrize("agent_only", [False, True], ids=["all-fields", "agent-visible"])
async def test_field_definitions_bind_the_tenant_before_the_optional_fragment(monkeypatch, tenant, agent_only):
    from app.domain.services.lead_capture_service import LeadCaptureService

    conn = Recorder()
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquirer(conn, []))
    await LeadCaptureService(object()).fields_for_campaign(tenant, CAMPAIGN, agent_only=agent_only)
    sql, args = only(conn.calls, "FROM campaign_lead_fields")
    assert bound_tenants(sql, args) == [tenant]
    assert ("AND agent_visible" in sql) is agent_only
    assert sql.index("tenant_id = $2") < sql.index("ORDER BY"), "the optional fragment is appended after the tenant predicate"


# -- services.action_execution: owned receipt lookup and the idempotent insert -----------------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
@pytest.mark.parametrize("by", ["action", "proposal"])
async def test_owned_action_receipt_lookup_binds_the_tenant_for_either_reference(monkeypatch, tenant, by):
    from app.services import action_execution

    conn = Recorder()
    monkeypatch.setattr(action_execution, "acquire_with_tenant", acquirer(conn, []))
    reference = {"action_id": FEEDBACK} if by == "action" else {"proposal_id": "proposal-1"}
    await action_execution.find_owned_action_receipt(
        SimpleNamespace(pool=object()), tenant_id=tenant, user_id=USER, **reference
    )
    sql, args = only(conn.calls, "FROM assistant_actions")
    assert bound_tenants(sql, args) == [tenant]
    assert "user_id=$2::uuid" in sql


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_durable_action_insert_and_reread_bind_the_tenant(monkeypatch, tenant):
    from app.services import action_execution

    conn = Recorder()  # no row from the insert and none from the re-read: the executor refuses
    monkeypatch.setattr(action_execution, "acquire_with_tenant", acquirer(conn, []))
    with pytest.raises(RuntimeError):
        await action_execution.DurableActionExecutor(object()).execute(
            tenant_id=tenant, idempotency_key="key-1", action="send_email", payload={"to": "x"}, executor=AsyncMock(),
        )
    insert, args = only(conn.calls, "INSERT INTO assistant_actions")
    assert insert_binding(insert, "tenant_id", args) == tenant
    assert "ON CONFLICT (tenant_id,idempotency_key)" in insert
    reread, args = only(conn.calls, "SELECT * FROM assistant_actions")
    assert bound_tenants(reread, args) == [tenant]


# -- campaign_knowledge_access: effective permissions ---------------------------------------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_effective_permissions_bind_the_tenant_and_only_add_global_grants(tenant):
    from app.domain.services import campaign_knowledge_access as access

    conn = Recorder()
    await access._current_permission_names(conn, actor_user_id=USER, tenant_id=tenant)
    sql, args = only(conn.calls, "FROM tenant_users tu")
    assert bound_tenants(sql, args) == [tenant, tenant]
    assert "(up.tenant_id IS NULL OR up.tenant_id = $2)" in sql, "the only widening is the global (NULL tenant) grant"


# -- legacy_pending_identity: the recovery sweep updates a row under that row's own tenant -----------


async def test_pending_identity_recovery_updates_each_row_under_its_own_tenant():
    from app.domain.services.telephony.legacy_pending_identity import reconcile_pending_identity

    conn = Recorder(execute_result="UPDATE 0")
    await reconcile_pending_identity(conn, "asterisk")
    sql, _ = only(conn.calls, "UPDATE calls c SET provider=$1")
    assert "c.tenant_id=identity.tenant_id" in sql


# -- telephony_concurrency_limiter: counts, the lease insert and the stale-lease reap ----------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_limiter_counts_and_reap_bind_the_tenant(tenant):
    from app.domain.services.telephony_concurrency_limiter import TelephonyConcurrencyLimiter

    conn = Recorder()
    limiter = TelephonyConcurrencyLimiter()
    await limiter._active_counts(conn, tenant_id=tenant)
    sql, args = only(conn.calls, "FROM tenant_telephony_concurrency_leases lease")
    assert bound_tenants(sql, args) == [tenant]
    conn.calls.clear()
    await limiter.expire_stale_leases(conn, tenant_id=tenant)
    sql, args = only(conn.calls, "UPDATE tenant_telephony_concurrency_leases")
    assert bound_tenants(sql, args) == [tenant]


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_limiter_lease_insert_binds_the_tenant_and_conflicts_only_within_it(tenant):
    from app.domain.services.telephony_concurrency_limiter import TelephonyConcurrencyLimiter

    conn = Recorder([("INSERT INTO tenant_telephony_concurrency_leases", {"id": UUID(FEEDBACK)})])
    decision = await TelephonyConcurrencyLimiter().acquire_lease(
        conn, tenant_id=tenant, call_id=CALL, talklee_call_id="tc-1", lease_kind="call",
    )
    assert decision.accepted is True
    sql, args = only(conn.calls, "INSERT INTO tenant_telephony_concurrency_leases")
    assert insert_binding(sql, "tenant_id", args) == tenant
    assert "ON CONFLICT (tenant_id, call_id, lease_kind)" in sql


# -- telephony_bridge: the outbound leg annotations go through the parent call's tenant --------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
@pytest.mark.parametrize("which", ["from_number", "route"])
async def test_outbound_leg_annotations_bind_the_calls_tenant(monkeypatch, tenant, which):
    from app.api.v1.endpoints import telephony_bridge

    conn = Recorder()
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquirer(conn, []))
    if which == "from_number":
        await telephony_bridge._record_outbound_from_number(
            object(), tenant_id=tenant, durable_call_id=CALL, from_number="+442046132300"
        )
    else:
        await telephony_bridge._record_outbound_route(
            object(), tenant_id=tenant, durable_call_id=CALL, route_meta={"trunk_id": TRUNK}
        )
    sql, args = only(conn.calls, "UPDATE call_legs l")
    assert bound_tenants(sql, args) == [tenant], "call_legs has no tenant column: the parent call must carry it"
    assert "FROM calls c WHERE c.id = l.call_id" in sql


# -- trunk_resolver.requires_sip_readiness ---------------------------------------------------------


@pytest.mark.parametrize("tenant", TENANTS, ids=IDS)
async def test_sip_readiness_binds_the_tenant_for_the_tenant_row_and_its_trunks(monkeypatch, tenant):
    from app.domain.services.telephony import trunk_resolver

    monkeypatch.setenv("TELEPHONY_ADAPTER", "asterisk")
    conn = Recorder()
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", acquirer(conn, []))
    assert await trunk_resolver.requires_sip_readiness(object(), tenant_id=tenant, campaign={}) is True
    sql, args = only(conn.calls, "FROM tenants WHERE id=$1::uuid")
    assert bound_tenants(sql, args) == [tenant]
    assert args == (tenant,)
