"""
Unit tests for:
  - manage_lead action='update' and revised action='remove' (soft-delete)
  - apply_campaign_voice (campaign_ai_options.py)

Kept in a separate file because test_assistant_campaign_admin.py is already
near the 600-line limit.
"""
from __future__ import annotations

import pytest

from app.infrastructure.assistant.tools.campaign_admin import manage_lead
from app.infrastructure.assistant.tools.campaign_ai_options import apply_campaign_voice


# ---------------------------------------------------------------------------
# Minimal fake db_client (same pattern as test_assistant_campaign_admin.py)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data=None, count=0):
        self.data = data or []
        self.count = count
        self.error = None


class _FakeQuery:
    def __init__(self, fake_db: "FakeDbClient", table: str, op: str, response: _FakeResponse):
        self._fake_db = fake_db
        self._table = table
        self._op = op
        self._response = response
        self._eq_calls: list = []
        self._payload = None

    def select(self, *args, **kwargs):
        return self

    def update(self, payload):
        self._payload = payload
        return self

    def insert(self, payload):
        self._payload = payload
        return self

    def delete(self):
        return self

    def eq(self, field, value):
        self._eq_calls.append((field, value))
        self._fake_db._record_eq(self._table, self._op, field, value)
        return self

    def neq(self, field, value):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, *args):
        return self

    def single(self):
        return self

    def execute(self):
        if self._op == "update":
            self._fake_db._update_calls.append(
                {"table": self._table, "payload": self._payload, "eq": self._eq_calls}
            )
        elif self._op == "insert":
            self._fake_db._insert_calls.append(
                {"table": self._table, "payload": self._payload}
            )
        elif self._op == "delete":
            self._fake_db._delete_calls.append(
                {"table": self._table, "eq": self._eq_calls}
            )
        return self._response


class FakeDbClient:
    def __init__(self):
        self._eq_record: list = []
        self._update_calls: list = []
        self._insert_calls: list = []
        self._delete_calls: list = []
        self._responses: dict = {}
        self.pool = None

    def configure_response(self, table: str, op: str, data=None, count=0):
        self._responses[(table, op)] = _FakeResponse(data=data, count=count)

    def _get_response(self, table: str, op: str) -> _FakeResponse:
        return self._responses.get((table, op), _FakeResponse())

    def _record_eq(self, table: str, op: str, field: str, value):
        self._eq_record.append((table, op, field, value))

    def table(self, tbl: str) -> "_TableProxy":
        return _TableProxy(self, tbl)


class _TableProxy:
    def __init__(self, fake_db: FakeDbClient, table: str):
        self._db = fake_db
        self._table = table

    def select(self, *args, **kwargs):
        q = _FakeQuery(self._db, self._table, "select", self._db._get_response(self._table, "select"))
        return q.select(*args, **kwargs)

    def update(self, payload):
        q = _FakeQuery(self._db, self._table, "update", self._db._get_response(self._table, "update"))
        return q.update(payload)

    def insert(self, payload):
        q = _FakeQuery(self._db, self._table, "insert", self._db._get_response(self._table, "insert"))
        return q.insert(payload)

    def delete(self):
        q = _FakeQuery(self._db, self._table, "delete", self._db._get_response(self._table, "delete"))
        return q.delete()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _campaign_db(campaign_id="c1"):
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": campaign_id}])
    return db


LEAD_ROW = {
    "id": "lead-1",
    "phone_number": "+15559999999",
    "first_name": "Bob",
    "last_name": "Smith",
    "email": "bob@example.com",
    "status": "pending",
    "campaign_id": "c1",
}


# ===========================================================================
# manage_lead — action="remove" (soft-delete)
# ===========================================================================


@pytest.mark.asyncio
async def test_manage_lead_remove_soft_delete_preview():
    """confirm=False for remove must preview the soft-delete (status→deleted), write nothing."""
    db = _campaign_db()
    db.configure_response("leads", "select", data=[LEAD_ROW.copy()])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="remove",
        lead_id="lead-1",
        confirm=False,
    )

    assert result.get("preview") is True
    # Nothing written
    assert db._update_calls == [], "update was called but confirm=False"
    assert db._delete_calls == [], "delete was called but confirm=False"

    change = result["changes"][0]
    assert change["before"]["id"] == "lead-1"
    # Preview shows the new state (status=deleted), not None (which was the old hard-delete preview)
    assert change["after"] == {"status": "deleted"}


@pytest.mark.asyncio
async def test_manage_lead_remove_soft_delete_confirm():
    """confirm=True for remove must call update with status='deleted', NOT hard delete."""
    db = _campaign_db()
    db.configure_response("leads", "select", data=[LEAD_ROW.copy()])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="remove",
        lead_id="lead-1",
        confirm=True,
    )

    assert result.get("applied") is True

    # Must use update (soft-delete), never hard delete
    assert db._delete_calls == [], "Hard delete was called — should be soft delete"
    assert len(db._update_calls) == 1

    update_call = db._update_calls[0]
    assert update_call["table"] == "leads"
    assert update_call["payload"] == {"status": "deleted"}, (
        f"Expected soft-delete payload {{status: deleted}}, got: {update_call['payload']}"
    )

    # Tenant scoped
    eq_fields = {f: v for (f, v) in update_call["eq"]}
    assert eq_fields.get("tenant_id") == "t1"
    assert eq_fields.get("id") == "lead-1"


# ===========================================================================
# manage_lead — action="update"
# ===========================================================================


@pytest.mark.asyncio
async def test_manage_lead_update_preview_no_write():
    """action=update, confirm=False → preview diff for phone_number change, writes nothing."""
    db = _campaign_db()
    db.configure_response(
        "leads",
        "select",
        data=[LEAD_ROW.copy()],
    )

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="update",
        lead_id="lead-1",
        phone_number="+15550001111",
        confirm=False,
    )

    assert result.get("preview") is True
    # Nothing written
    assert db._update_calls == [], "update was called but confirm=False"
    assert db._insert_calls == []
    assert db._delete_calls == []

    changes = result.get("changes", [])
    phone_change = next((c for c in changes if c["field"] == "phone_number"), None)
    assert phone_change is not None, f"phone_number not in diff: {changes}"
    assert phone_change["before"] == "+15559999999"
    assert phone_change["after"] == "+15550001111"

    # Note must mention confirm=true
    assert "confirm=true" in result.get("note", "").lower()


@pytest.mark.asyncio
async def test_manage_lead_update_confirm_applies_changed_fields():
    """action=update, confirm=True → calls update once with only changed fields."""
    db = _campaign_db()
    db.configure_response("leads", "select", data=[LEAD_ROW.copy()])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="update",
        lead_id="lead-1",
        phone_number="+15550001111",
        first_name="Robert",
        confirm=True,
    )

    assert result.get("applied") is True
    assert len(db._update_calls) == 1

    payload = db._update_calls[0]["payload"]
    assert payload["phone_number"] == "+15550001111"
    assert payload["first_name"] == "Robert"
    # last_name and email not supplied → must NOT be in payload
    assert "last_name" not in payload
    assert "email" not in payload

    # Tenant + lead ID scoping
    eq_fields = {f: v for (f, v) in db._update_calls[0]["eq"]}
    assert eq_fields.get("tenant_id") == "t1"
    assert eq_fields.get("id") == "lead-1"


@pytest.mark.asyncio
async def test_manage_lead_update_no_fields_returns_error():
    """action=update with no fields → error, no write."""
    db = _campaign_db()
    db.configure_response("leads", "select", data=[LEAD_ROW.copy()])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="update",
        lead_id="lead-1",
        confirm=False,
    )

    assert "error" in result
    assert db._update_calls == []


@pytest.mark.asyncio
async def test_manage_lead_update_lead_not_found():
    """action=update with unknown lead_id → error."""
    db = _campaign_db()
    db.configure_response("leads", "select", data=[])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="update",
        lead_id="ghost-lead",
        phone_number="+15550001111",
        confirm=False,
    )

    assert result == {"error": "lead not found"}
    assert db._update_calls == []


@pytest.mark.asyncio
async def test_manage_lead_update_no_change_detected():
    """action=update with same value as current → no-change preview, no write."""
    db = _campaign_db()
    db.configure_response(
        "leads",
        "select",
        data=[LEAD_ROW.copy()],  # phone is +15559999999
    )

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="update",
        lead_id="lead-1",
        phone_number="+15559999999",  # same as current
        confirm=False,
    )

    assert result.get("preview") is True
    assert result.get("changes") == []
    assert db._update_calls == []


# ===========================================================================
# apply_campaign_voice
# ===========================================================================


async def _fake_catalog(provider: str):
    # apply_campaign_voice now resolves names→ids via _voice_catalog_for_provider.
    return [
        {"id": "voice-valid-1", "name": "Valid One"},
        {"id": "voice-valid-2", "name": "Valid Two"},
    ]


@pytest.mark.asyncio
async def test_apply_campaign_voice_invalid_voice_no_write(monkeypatch):
    """An invalid voice_id for the provider must return an error and write nothing."""
    import app.infrastructure.assistant.tools.campaign_ai_options as mod

    monkeypatch.setattr(
        "app.infrastructure.assistant.tools.campaign_ai_options._voice_catalog_for_provider",
        _fake_catalog,
    )

    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": "c1", "name": "Camp 1",
                                                         "tts_provider": "google",
                                                         "voice_id": "voice-old"}])

    result = await apply_campaign_voice(
        tenant_id="t1",
        db_client=db,
        campaign_ids=["c1"],
        tts_provider="google",
        voice_id="voice-bogus-123",
        confirm=False,
    )

    assert "error" in result
    assert "voice-bogus-123" in result["error"]
    assert db._update_calls == []


@pytest.mark.asyncio
async def test_apply_campaign_voice_valid_preview(monkeypatch):
    """Valid voice, confirm=False → preview diff, no write."""
    monkeypatch.setattr(
        "app.infrastructure.assistant.tools.campaign_ai_options._voice_catalog_for_provider",
        _fake_catalog,
    )

    db = FakeDbClient()
    db.configure_response(
        "campaigns",
        "select",
        data=[{"id": "c1", "name": "Camp 1", "tts_provider": "google", "voice_id": "voice-old"}],
    )

    result = await apply_campaign_voice(
        tenant_id="t1",
        db_client=db,
        campaign_ids=["c1"],
        tts_provider="google",
        voice_id="voice-valid-1",
        confirm=False,
    )

    assert result.get("preview") is True
    assert db._update_calls == []

    campaigns = result.get("campaigns", [])
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c["campaign_id"] == "c1"

    provider_change = next((ch for ch in c["changes"] if ch["field"] == "tts_provider"), None)
    voice_change = next((ch for ch in c["changes"] if ch["field"] == "voice_id"), None)
    assert provider_change is not None
    assert provider_change["before"] == "google"
    assert provider_change["after"] == "google"
    assert voice_change is not None
    assert voice_change["before"] == "voice-old"
    assert voice_change["after"] == "voice-valid-1"

    assert "confirm=true" in result.get("note", "").lower()


@pytest.mark.asyncio
async def test_apply_campaign_voice_valid_confirm_updates_each(monkeypatch):
    """Valid voice, confirm=True → updates each campaign with new tts_provider + voice_id."""
    monkeypatch.setattr(
        "app.infrastructure.assistant.tools.campaign_ai_options._voice_catalog_for_provider",
        _fake_catalog,
    )

    db = FakeDbClient()
    # Two campaigns — configure select to return per-call (FakeDbClient returns same
    # response for table+op; we configure once and both look up "campaigns"+"select")
    db.configure_response(
        "campaigns",
        "select",
        data=[{"id": "c1", "name": "Camp 1", "tts_provider": "google", "voice_id": "voice-old"}],
    )

    result = await apply_campaign_voice(
        tenant_id="t1",
        db_client=db,
        campaign_ids=["c1", "c2"],
        tts_provider="deepgram",
        voice_id="voice-valid-2",
        confirm=True,
    )

    assert result.get("applied") is True
    assert result["tts_provider"] == "deepgram"
    assert result["voice_id"] == "voice-valid-2"

    # Two update calls — one per campaign
    assert len(db._update_calls) == 2

    for call in db._update_calls:
        assert call["table"] == "campaigns"
        assert call["payload"] == {"tts_provider": "deepgram", "voice_id": "voice-valid-2"}
        eq_fields = {f: v for (f, v) in call["eq"]}
        assert eq_fields.get("tenant_id") == "t1"


@pytest.mark.asyncio
async def test_apply_campaign_voice_campaign_not_found(monkeypatch):
    """campaign_id not under tenant → error, no write."""
    monkeypatch.setattr(
        "app.infrastructure.assistant.tools.campaign_ai_options._voice_catalog_for_provider",
        _fake_catalog,
    )

    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[])  # nothing found

    result = await apply_campaign_voice(
        tenant_id="t1",
        db_client=db,
        campaign_ids=["ghost-id"],
        tts_provider="google",
        voice_id="voice-valid-1",
        confirm=True,
    )

    assert "error" in result
    assert "ghost-id" in result["error"]
    assert db._update_calls == []


@pytest.mark.asyncio
async def test_apply_campaign_voice_empty_list(monkeypatch):
    """Empty campaign_ids → error immediately."""
    monkeypatch.setattr(
        "app.infrastructure.assistant.tools.campaign_ai_options._voice_catalog_for_provider",
        _fake_catalog,
    )

    db = FakeDbClient()
    result = await apply_campaign_voice(
        tenant_id="t1",
        db_client=db,
        campaign_ids=[],
        tts_provider="google",
        voice_id="voice-valid-1",
        confirm=False,
    )

    assert "error" in result
    assert db._update_calls == []
