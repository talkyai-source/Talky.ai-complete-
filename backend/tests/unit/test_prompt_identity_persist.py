"""Does the prompt identity actually land on the dialer's ``calls`` row?

An audit claimed ``calls.prompt_version`` is NULL on every outbound call
because ``voice_orchestrator`` joins on a ``talklee_call_id`` the dialer never
wrote.  **That stated mechanism is wrong**: ``dialer_worker._create_call_intent``
does generate a ``talklee_call_id`` and the ``INSERT INTO calls`` column list
does include it (pinned by ``test_the_dialer_does_write_talklee_call_id``).

The conclusion was still right, for a different reason, and it is settled here
by executing both real construction paths against one in-memory ``calls``
table instead of by argument:

    dialer_worker._create_call_intent()   -> INSERT INTO calls (talklee_call_id = A)
    VoiceOrchestrator.create_voice_session() -> mints talklee_call_id = B

A and B are two independent ``generate_talklee_call_id()`` results, so an
UPDATE keyed on B can never reach the row keyed on A.  The two ids are never
handed to each other in these tests — each comes out of the real code.

The durable join the telephony bridge *does* establish is
``voice_session._dialer_call_id`` = ``calls.id`` (set by
``call_transcript_persister.bind_telephony_call`` for outbound and from the
admission snapshot for true inbound), so that is the key the persist has to
use.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from app.domain.models.dialer_job import DialerJob
from app.domain.services.telephony_session_config import (
    build_telephony_session_config,
)
from app.domain.services.voice_orchestrator import VoiceOrchestrator
from app.workers.dialer_worker import DialerWorker

TENANT = "11111111-1111-1111-1111-111111111111"

_WHERE_RE = re.compile(r"WHERE (\w+) = \$1", re.IGNORECASE)


# ---------------------------------------------------------------------------
# A very small in-memory `calls` table. It understands exactly the two
# statements under test: the dialer's INSERT and the orchestrator's UPDATE.
# ---------------------------------------------------------------------------
class FakeCallsDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.updates: list[tuple[str, Any, int]] = []  # (where_col, key, matched)
        self.session_sql: list[str] = []

    def find(self, column: str, value: Any) -> list[dict[str, Any]]:
        return [r for r in self.rows if str(r.get(column)) == str(value)]


class _NullCtx:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return None


class FakeConn:
    def __init__(self, db: FakeCallsDB) -> None:
        self._db = db

    def transaction(self):
        return _NullCtx()

    async def execute(self, query: str, *args: Any) -> str:
        q = " ".join(query.split())
        upper = q.upper()

        if upper.startswith("SET"):
            self._db.session_sql.append(q)
            return "SET"

        if upper.startswith("INSERT INTO CALLS "):
            cols = [
                c.strip()
                for c in q[q.index("(") + 1: q.index(")")].split(",")
            ]
            # created_at is NOW(), not a bound arg — zip truncates it away.
            self._db.rows.append(dict(zip(cols, args)))
            return "INSERT 0 1"

        if upper.startswith("UPDATE CALLS"):
            m = _WHERE_RE.search(q)
            assert m, f"unsupported UPDATE in test double: {q}"
            col = m.group(1)
            matched = self._db.find(col, args[0])
            for row in matched:
                row["prompt_template"] = args[1]
                row["prompt_version"] = args[2]
                row["prompt_hash"] = args[3]
            self._db.updates.append((col, args[0], len(matched)))
            return f"UPDATE {len(matched)}"

        return "OK"

    async def fetchrow(self, query: str, *args: Any):
        q = " ".join(query.split())
        upper = q.upper()
        if upper.startswith("INSERT INTO CALLS "):
            row = {
                "id": args[0],
                "tenant_id": args[1],
                "campaign_id": args[2],
                "lead_id": args[3],
                "phone_number": args[4],
                "status": "initiated",
                "talklee_call_id": args[5],
                "dialer_job_id": args[6],
                "dialer_attempt_number": args[7],
                "direction": "outbound",
            }
            self._db.rows.append(row)
            return {
                "id": row["id"],
                "talklee_call_id": row["talklee_call_id"],
                "status": "initiated",
                "provider_call_id": None,
            }
        raise AssertionError(f"unsupported fetchrow in test double: {q}")


class _ConnCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *exc):
        return None


class FakePool:
    def __init__(self, db: FakeCallsDB) -> None:
        self._db = db

    def acquire(self, *a: Any, **kw: Any) -> _ConnCtx:
        return _ConnCtx(FakeConn(self._db))


class FakeDBClient:
    """Deliberately has no ``table()`` — that is what keeps call-event logging
    (which needs a Supabase-shaped client) out of these tests."""

    def __init__(self, pool: FakePool) -> None:
        self.pool = pool


def _stub_providers(orch: VoiceOrchestrator) -> None:
    """Replace only the four provider factories. Everything that computes or
    carries the ids — the talklee mint, CallSession, VoiceSession, the persist
    — is the real code."""

    async def _mk(_config: Any) -> AsyncMock:
        return AsyncMock()

    orch._create_stt_provider = _mk       # type: ignore[assignment]
    orch._create_llm_provider = _mk       # type: ignore[assignment]
    orch._create_tts_provider = _mk       # type: ignore[assignment]
    orch._create_media_gateway = _mk      # type: ignore[assignment]


def _job() -> DialerJob:
    return DialerJob(
        job_id=str(uuid.uuid4()),
        campaign_id=str(uuid.uuid4()),
        lead_id=str(uuid.uuid4()),
        tenant_id=TENANT,
        phone_number="+15551234567",
    )


async def _dial(db: FakeCallsDB) -> tuple[str, str]:
    """Run the REAL dialer insert. Returns (calls.id, talklee_call_id)."""
    worker = DialerWorker()
    worker._db_pool = FakePool(db)  # type: ignore[assignment]
    intent = await worker._create_call_intent(_job())
    return intent.call_id, intent.talklee_call_id


async def _session(db: FakeCallsDB, orch: Optional[VoiceOrchestrator] = None):
    """Run the REAL session construction. Returns (orchestrator, session, config)."""
    config = build_telephony_session_config(gateway_type="telephony")
    orch = orch or VoiceOrchestrator(db_client=FakeDBClient(FakePool(db)))
    _stub_providers(orch)
    session = await orch.create_voice_session(config)
    return orch, session, config


# ---------------------------------------------------------------------------
# The refutation half of the audit claim
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_dialer_does_write_talklee_call_id():
    db = FakeCallsDB()
    internal_call_id, talklee = await _dial(db)

    assert db.rows, "expected an INSERT INTO calls"
    row = db.rows[0]
    assert row["id"] == internal_call_id
    assert row["talklee_call_id"] == talklee
    assert talklee.startswith("tlk_")


# ---------------------------------------------------------------------------
# The real defect
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_orchestrator_mints_its_own_talklee_id_not_the_dialers():
    """Pins the actual mechanism so nobody 'fixes' this by assuming the two
    ids are equal: they are two independent generate_talklee_call_id() calls
    and nothing carries the dialer's onto the session config."""
    db = FakeCallsDB()
    _internal, dialer_talklee = await _dial(db)
    _orch, session, _config = await _session(db)

    assert session.talklee_call_id != dialer_talklee
    assert not db.find("talklee_call_id", session.talklee_call_id)


@pytest.mark.asyncio
async def test_prompt_identity_lands_on_the_dialers_calls_row():
    """The contract: after a campaign call's full session lifecycle, the row
    the dialer created carries the prompt identity the session actually ran."""
    db = FakeCallsDB()
    internal_call_id, _dialer_talklee = await _dial(db)
    row = db.rows[0]
    assert row.get("prompt_version") is None

    orch, session, config = await _session(db)
    assert config.prompt_hash, "session config must carry a prompt identity"

    # Exactly what telephony/lifecycle.py does after answer: bind the durable
    # calls row onto the session (bind_telephony_call for outbound, the
    # admission snapshot for true inbound).
    session._dialer_call_id = internal_call_id
    session._dialer_tenant_id = TENANT

    await orch.end_session(session)

    assert row["prompt_version"] == config.prompt_version
    assert row["prompt_hash"] == config.prompt_hash
    assert row["prompt_template"] == config.prompt_template


@pytest.mark.asyncio
async def test_persist_runs_under_the_bound_tenant_context():
    """The write must carry tenant context, not rely on the app role's
    BYPASSRLS (see CLAUDE.md: prod RLS was decorative)."""
    db = FakeCallsDB()
    internal_call_id, _ = await _dial(db)
    orch, session, _config = await _session(db)
    session._dialer_call_id = internal_call_id
    session._dialer_tenant_id = TENANT

    await orch.end_session(session)

    assert any(
        TENANT in s and "app.current_tenant_id" in s for s in db.session_sql
    ), db.session_sql


@pytest.mark.asyncio
async def test_no_durable_row_no_write():
    """Pre-warm / ringing / orphan sessions are ended without ever being bound
    to a calls row. They must not UPDATE anything (and must not log a
    matched-no-row warning, which would make the signal constant)."""
    db = FakeCallsDB()
    await _dial(db)
    orch, session, _config = await _session(db)

    await orch.end_session(session)

    assert db.updates == []
    assert db.rows[0].get("prompt_version") is None


@pytest.mark.asyncio
async def test_session_without_prompt_identity_writes_nothing():
    """ask-AI / browser-assistant sessions build their config elsewhere and
    legitimately have no campaign prompt — writing empty strings over a row
    would be worse than writing nothing."""
    db = FakeCallsDB()
    internal_call_id, _ = await _dial(db)
    orch, session, config = await _session(db)
    config.prompt_template = ""
    config.prompt_version = ""
    config.prompt_hash = ""
    session._dialer_call_id = internal_call_id
    session._dialer_tenant_id = TENANT

    await orch.end_session(session)

    assert db.updates == []
    assert db.rows[0].get("prompt_version") is None
