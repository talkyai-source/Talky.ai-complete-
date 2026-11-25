"""A database statement with no account named and no bypass is logged, not silent.

Behaviour is otherwise unchanged: same statements, same nil tenant, no exception.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from app.core import db as core_db
from app.core import postgres_adapter
from app.core.postgres_adapter import QueryBuilder, RpcBuilder
from app.core.security import rls_context_guard as guard
from app.core.security import tenant_isolation as ti

NIL = "00000000-0000-0000-0000-000000000000"
LOGGER = "app.core.security.rls_context_guard"


@pytest.fixture(autouse=True)
def _clean_state():
    guard._reset_for_tests()
    ti.clear_tenant_context()
    yield
    guard._reset_for_tests()
    ti.clear_tenant_context()


def _lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.getMessage().startswith("rls_context_missing")
    ]


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(guard.time, "monotonic", c)
    return c


# ---------------------------------------------------------------- the guard itself


def test_first_call_logs_with_suppressed_zero(caplog, clock):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    guard.note_missing_context("get_db", "calls")
    lines = _lines(caplog)
    assert len(lines) == 1
    assert lines[0].startswith("rls_context_missing source=get_db target=calls where=")
    assert lines[0].endswith("suppressed=0")


def test_second_call_inside_window_logs_nothing(caplog, clock):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    guard.note_missing_context("get_db", "calls")
    clock.now += 299
    guard.note_missing_context("get_db", "calls")
    assert len(_lines(caplog)) == 1


def test_after_window_reports_the_suppressed_count_then_resets(caplog, clock):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    guard.note_missing_context("get_db", "calls")
    for _ in range(3):
        clock.now += 10
        guard.note_missing_context("get_db", "calls")
    clock.now += 300
    guard.note_missing_context("get_db", "calls")
    lines = _lines(caplog)
    assert len(lines) == 2
    assert lines[1].endswith("suppressed=3")
    clock.now += 300
    guard.note_missing_context("get_db", "calls")
    assert _lines(caplog)[2].endswith("suppressed=0")


def test_different_targets_each_log(caplog, clock):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    guard.note_missing_context("old_client", "calls")
    guard.note_missing_context("old_client", "contacts")
    guard.note_missing_context("get_db")
    assert len(_lines(caplog)) == 3


def test_unsafe_target_is_cleaned_and_cut(caplog, clock):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    guard.note_missing_context("old_client", "ca'lls\nx y\"z.t" + "a" * 200)
    line = _lines(caplog)[0]
    target = line.split(" target=")[1].split(" where=")[0]
    assert target.startswith("callsxyz.t")
    assert len(target) == 64
    assert "\n" not in line
    guard.note_missing_context("old_client", "'' \n")
    assert " target=- " in _lines(caplog)[1]


def test_257th_distinct_pair_is_counted_under_other(caplog, clock):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    for i in range(256):
        guard.note_missing_context("old_client", f"t{i}")
    assert len(_lines(caplog)) == 256
    guard.note_missing_context("old_client", "brand_new")
    guard.note_missing_context("old_client", "brand_new_two")
    lines = _lines(caplog)
    assert len(lines) == 257
    assert " target=other " in lines[256]
    assert len(guard._state) == 257  # 256 tracked + the one "other" bucket


def test_an_exception_inside_never_propagates(monkeypatch, clock):
    def boom(*_a, **_k):
        raise RuntimeError("logger broke")

    monkeypatch.setattr(guard.logger, "warning", boom)
    guard.note_missing_context("get_db", "calls")  # must not raise


def test_where_is_dash_when_the_caller_is_not_under_app(caplog, clock):
    # This test file is under tests/, not app/, so no caller qualifies.
    caplog.set_level(logging.WARNING, logger=LOGGER)
    guard.note_missing_context("get_db", "calls")
    assert " where=- " in _lines(caplog)[0]


def test_where_names_the_first_app_caller_outside_core(caplog, clock):
    # Run a function whose code object claims to live in app/services/.
    caplog.set_level(logging.WARNING, logger=LOGGER)
    namespace = {"note": guard.note_missing_context}
    code = compile(
        "def caller():\n    note('get_db', 'calls')\n",
        "C:\\repo\\backend\\app\\services\\email_service.py",
        "exec",
    )
    exec(code, namespace)
    namespace["caller"]()
    assert " where=services/email_service.py:2 " in _lines(caplog)[0]


# ---------------------------------------------------------------- get_db wiring


class _FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return []

    async def set_type_codec(self, *_a, **_k):
        return None

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_get_db_context_with_nothing_set_is_logged_and_unchanged(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    conn = _FakeConn()
    await core_db._apply_rls_context(conn, None, False)
    assert conn.calls == [
        ("SELECT set_config('app.current_tenant_id', $1, true)", (NIL,)),
        ("SELECT set_config('app.bypass_rls', 'false', true)", ()),
    ]
    lines = _lines(caplog)
    assert len(lines) == 1
    assert "source=get_db target=-" in lines[0]


@pytest.mark.asyncio
async def test_get_db_context_with_a_tenant_or_bypass_logs_nothing(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    await core_db._apply_rls_context(_FakeConn(), str(uuid.uuid4()), False)
    await core_db._apply_rls_context(_FakeConn(), None, True)
    assert _lines(caplog) == []


# ---------------------------------------------------------------- old client wiring


def _use_fake_connection(monkeypatch) -> _FakeConn:
    conn = _FakeConn()

    async def fake_connect(_dsn, **_kwargs):
        return conn

    monkeypatch.setattr(postgres_adapter.asyncpg, "connect", fake_connect)
    monkeypatch.setitem(postgres_adapter._TABLE_COLUMN_TYPES_CACHE, "calls", {"id": "text"})
    return conn


def _set_calls(conn: _FakeConn) -> list[str]:
    return [sql for sql, _ in conn.calls if sql.startswith("SET LOCAL")]


@pytest.mark.asyncio
async def test_old_client_query_with_nothing_set_is_logged_and_unchanged(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    conn = _use_fake_connection(monkeypatch)
    response = await QueryBuilder(None, "calls").select("id").eq("id", "x")._execute_async()
    assert response.error is None
    assert _set_calls(conn) == [
        f"SET LOCAL app.current_tenant_id = '{NIL}'",
        "SET LOCAL app.bypass_rls = 'false'",
    ]
    lines = _lines(caplog)
    assert len(lines) == 1
    assert "source=old_client target=calls" in lines[0]


@pytest.mark.asyncio
async def test_old_client_query_with_tenant_or_bypass_logs_nothing(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    conn = _use_fake_connection(monkeypatch)
    tenant = str(uuid.uuid4())
    ti.set_current_tenant_id(tenant)
    await QueryBuilder(None, "calls").select("id")._execute_async()
    assert _set_calls(conn)[0] == f"SET LOCAL app.current_tenant_id = '{tenant}'"
    ti.clear_tenant_context()
    ti.set_bypass_rls(True)
    await QueryBuilder(None, "calls").select("id")._execute_async()
    assert _lines(caplog) == []


@pytest.mark.asyncio
async def test_old_client_rpc_with_nothing_set_is_logged_and_unchanged(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    conn = _use_fake_connection(monkeypatch)

    async def fake_with_conn(self, _conn):
        return postgres_adapter.PostgrestResponse(data=[])

    monkeypatch.setattr(RpcBuilder, "_execute_with_conn", fake_with_conn)
    response = await RpcBuilder(None, "increment_campaign_counter", {})._execute_async()
    assert response.error is None
    assert _set_calls(conn) == [
        f"SET LOCAL app.current_tenant_id = '{NIL}'",
        "SET LOCAL app.bypass_rls = 'false'",
    ]
    lines = _lines(caplog)
    assert len(lines) == 1
    assert "source=old_client_rpc target=increment_campaign_counter" in lines[0]

    guard._reset_for_tests()
    caplog.clear()
    ti.set_bypass_rls(True)
    await RpcBuilder(None, "increment_campaign_counter", {})._execute_async()
    assert _lines(caplog) == []


# ------------------------------------------- get_db: one window per caller, not one for all


def _get_db_caller(path: str, name: str):
    # A function whose code object claims to live at ``path``.
    source = chr(10).join([
        f"def {name}(note):",
        "    note('get_db')",
        "",
    ])
    namespace: dict = {}
    exec(compile(source, path, "exec"), namespace)
    return namespace[name]


def test_two_get_db_callers_in_one_window_are_both_named(caplog, clock):
    # get_db passes no target. With one window for all of them, a second blind
    # caller was only a "suppressed" count and never named (review, 25 Nov).
    caplog.set_level(logging.WARNING, logger=LOGGER)
    first = _get_db_caller("/opt/talky/backend/app/domain/services/telephony/prewarm.py", "first")
    second = _get_db_caller("/opt/talky/backend/app/domain/repositories/call_event_repository.py", "second")
    first(guard.note_missing_context)
    second(guard.note_missing_context)
    first(guard.note_missing_context)  # the same caller again: counted, not logged
    lines = _lines(caplog)
    assert len(lines) == 2
    assert "source=get_db target=- where=domain/services/telephony/prewarm.py:2 suppressed=0" in lines[0]
    assert "source=get_db target=- where=domain/repositories/call_event_repository.py:2 suppressed=0" in lines[1]

    clock.now += 301
    first(guard.note_missing_context)
    assert "where=domain/services/telephony/prewarm.py:2 suppressed=1" in _lines(caplog)[2]


def test_a_named_target_still_has_one_window_whoever_calls(caplog, clock):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    guard.note_missing_context("old_client", "calls")
    guard.note_missing_context("old_client", "calls")
    assert len(_lines(caplog)) == 1
