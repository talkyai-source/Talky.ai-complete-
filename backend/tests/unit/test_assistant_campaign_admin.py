"""
Unit tests for campaign_admin assistant tools.

Covers:
- get_campaign_detail: tenant scoping (eq("tenant_id", X) is always called with
  the passed tenant, not anything from args)
- update_campaign_config confirm=False: returns preview=True, writes NOTHING,
  correct before→after diff for a persona_type change
- update_campaign_config confirm=True: calls builder update once with a merged
  script_config (untouched keys preserved)
"""
from __future__ import annotations

import pytest

from app.infrastructure.assistant.tools.campaign_admin import (
    get_campaign_detail,
    get_knowledge_tree,
    manage_lead,
    update_campaign_config,
)


# ---------------------------------------------------------------------------
# Minimal fake db_client (builder chain: table → select/update/insert/delete
# → eq → eq → ... → execute)
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data=None, count=0):
        self.data = data or []
        self.count = count
        self.error = None


class _FakeQuery:
    """Records every eq() call and returns self for chaining.  execute() returns
    a pre-configured response."""

    def __init__(self, fake_db: "FakeDbClient", table: str, op: str, response: _FakeResponse):
        self._fake_db = fake_db
        self._table = table
        self._op = op
        self._response = response
        self._eq_calls: list = []
        self._payload = None

    # chaining helpers
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
    """Minimal fake that supports table().select/update/insert/delete chaining."""

    def __init__(self):
        self._eq_record: list = []       # all eq(field, value) calls ever
        self._update_calls: list = []
        self._insert_calls: list = []
        self._delete_calls: list = []
        # responses keyed by table+op
        self._responses: dict = {}
        # pool is unused in these tests (no pool-based calls)
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
# Tests: get_campaign_detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_campaign_detail_tenant_scoped():
    """The builder must always call eq('tenant_id', <passed tenant>)."""
    db = FakeDbClient()
    db.configure_response(
        "campaigns",
        "select",
        data=[
            {
                "id": "camp-1",
                "name": "My Campaign",
                "status": "running",
                "voice_id": "v1",
                "tts_provider": "google",
                "knowledge_mode": "off",
                "knowledge_model": None,
                "goal": "sales",
                "script_config": {},
            }
        ],
    )

    result = await get_campaign_detail(
        tenant_id="tenant-abc",
        db_client=db,
        campaign_id="camp-1",
    )

    assert result.get("campaign") is not None
    assert result["campaign"]["id"] == "camp-1"

    # Confirm eq("tenant_id", "tenant-abc") was called (not "tenant-xyz" or anything else)
    tenant_eq_calls = [
        (f, v)
        for (tbl, op, f, v) in db._eq_record
        if f == "tenant_id"
    ]
    assert len(tenant_eq_calls) >= 1, "eq('tenant_id', ...) was never called"
    for field, value in tenant_eq_calls:
        assert value == "tenant-abc", f"tenant_id eq was called with wrong value: {value!r}"


@pytest.mark.asyncio
async def test_get_campaign_detail_not_found():
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[])

    result = await get_campaign_detail(
        tenant_id="tenant-abc",
        db_client=db,
        campaign_id="no-such-campaign",
    )
    assert result == {"error": "campaign not found"}


@pytest.mark.asyncio
async def test_get_campaign_detail_no_id_or_name():
    db = FakeDbClient()
    result = await get_campaign_detail(tenant_id="t1", db_client=db)
    assert "error" in result


# ---------------------------------------------------------------------------
# Tests: update_campaign_config — preview (confirm=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_campaign_config_preview_no_write():
    """confirm=False must return preview=True and NEVER call update."""
    db = FakeDbClient()
    db.configure_response(
        "campaigns",
        "select",
        data=[
            {
                "id": "camp-1",
                "name": "Original Name",
                "goal": "sales",
                "script_config": {
                    "persona_type": "friendly",
                    "company_name": "Acme",
                    "agent_names": ["Alice"],
                },
            }
        ],
    )

    result = await update_campaign_config(
        tenant_id="tenant-abc",
        db_client=db,
        campaign_id="camp-1",
        changes={"persona_type": "professional"},
        confirm=False,
    )

    # Must be a preview
    assert result.get("preview") is True, f"Expected preview=True, got: {result}"

    # Must NOT have written anything
    assert db._update_calls == [], "update was called but confirm=False"
    assert db._insert_calls == [], "insert was called but confirm=False"

    # Must contain a correct before→after for persona_type
    changes = result.get("changes", [])
    persona_change = next(
        (c for c in changes if c["field"] == "script_config.persona_type"), None
    )
    assert persona_change is not None, f"persona_type change not in diff: {changes}"
    assert persona_change["before"] == "friendly"
    assert persona_change["after"] == "professional"

    # Note must mention confirm=true
    assert "confirm=true" in result.get("note", "").lower()


@pytest.mark.asyncio
async def test_update_campaign_config_preview_top_level_diff():
    """Top-level key (name) change also appears in preview diff."""
    db = FakeDbClient()
    db.configure_response(
        "campaigns",
        "select",
        data=[
            {
                "id": "camp-2",
                "name": "Old Name",
                "goal": "leads",
                "script_config": {},
            }
        ],
    )

    result = await update_campaign_config(
        tenant_id="t1",
        db_client=db,
        campaign_id="camp-2",
        changes={"name": "New Name"},
        confirm=False,
    )

    assert result.get("preview") is True
    assert db._update_calls == []

    name_change = next(
        (c for c in result.get("changes", []) if c["field"] == "name"), None
    )
    assert name_change is not None
    assert name_change["before"] == "Old Name"
    assert name_change["after"] == "New Name"


# ---------------------------------------------------------------------------
# Tests: update_campaign_config — apply (confirm=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_campaign_config_confirm_merges_script_config():
    """confirm=True must call update exactly once with a MERGED script_config
    (untouched keys preserved)."""
    db = FakeDbClient()
    db.configure_response(
        "campaigns",
        "select",
        data=[
            {
                "id": "camp-3",
                "name": "My Camp",
                "goal": "sales",
                "script_config": {
                    "persona_type": "friendly",
                    "company_name": "Acme Corp",
                    "agent_names": ["Alice", "Bob"],
                    "additional_instructions": "Be polite.",
                    "knowledge_driven": False,
                },
            }
        ],
    )

    result = await update_campaign_config(
        tenant_id="t1",
        db_client=db,
        campaign_id="camp-3",
        changes={"persona_type": "professional"},
        confirm=True,
    )

    assert result.get("applied") is True

    # Exactly one update call
    assert len(db._update_calls) == 1

    update_payload = db._update_calls[0]["payload"]
    assert "script_config" in update_payload

    merged_sc = update_payload["script_config"]
    # Changed key
    assert merged_sc["persona_type"] == "professional"
    # Untouched keys must be preserved
    assert merged_sc["company_name"] == "Acme Corp"
    assert merged_sc["agent_names"] == ["Alice", "Bob"]
    assert merged_sc["additional_instructions"] == "Be polite."
    assert merged_sc["knowledge_driven"] is False


@pytest.mark.asyncio
async def test_update_campaign_config_drops_voice_keys():
    """voice_id and tts_provider must be silently dropped with a warning."""
    db = FakeDbClient()
    db.configure_response(
        "campaigns",
        "select",
        data=[
            {
                "id": "camp-4",
                "name": "Camp",
                "goal": "sales",
                "script_config": {"persona_type": "friendly"},
            }
        ],
    )

    result = await update_campaign_config(
        tenant_id="t1",
        db_client=db,
        campaign_id="camp-4",
        changes={"voice_id": "v-new", "tts_provider": "elevenlabs", "goal": "leads"},
        confirm=False,
    )

    # Should warn about dropped keys
    warnings = result.get("warnings", [])
    assert any("voice" in w.lower() for w in warnings), f"No voice warning: {result}"

    # Only goal should appear in diff (not voice_id / tts_provider)
    diff_fields = {c["field"] for c in result.get("changes", [])}
    assert "voice_id" not in diff_fields
    assert "tts_provider" not in diff_fields
    assert "goal" in diff_fields


@pytest.mark.asyncio
async def test_update_campaign_config_not_found():
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[])

    result = await update_campaign_config(
        tenant_id="t1",
        db_client=db,
        campaign_id="ghost",
        changes={"goal": "new goal"},
        confirm=False,
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# Tests: get_knowledge_tree tenant-gated via campaign ownership
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_knowledge_tree_campaign_not_found():
    """Should return error if campaign doesn't belong to tenant."""
    db = FakeDbClient()
    # campaign ownership check returns nothing
    db.configure_response("campaigns", "select", data=[])

    result = await get_knowledge_tree(
        tenant_id="t1",
        db_client=db,
        campaign_id="camp-ghost",
    )
    assert result == {"error": "campaign not found"}


# ---------------------------------------------------------------------------
# Tests: manage_lead
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manage_lead_add_preview_no_write():
    """action=add, confirm=False → preview, no insert."""
    db = FakeDbClient()
    # campaign ownership
    db.configure_response("campaigns", "select", data=[{"id": "c1"}])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="add",
        name="John Doe",
        phone_number="+15551234567",
        confirm=False,
    )
    assert result.get("preview") is True
    assert db._insert_calls == []

    change = result["changes"][0]
    assert change["before"] is None
    assert change["after"]["phone_number"] == "+15551234567"
    assert change["after"]["first_name"] == "John"
    assert change["after"]["last_name"] == "Doe"


@pytest.mark.asyncio
async def test_manage_lead_add_confirm_inserts():
    """action=add, confirm=True → inserts lead."""
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": "c1"}])
    db.configure_response(
        "leads",
        "insert",
        data=[{"id": "lead-xyz", "phone_number": "+15551234567"}],
    )

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="add",
        name="Jane",
        phone_number="+15551234567",
        confirm=True,
    )
    assert result.get("applied") is True
    assert len(db._insert_calls) == 1
    inserted = db._insert_calls[0]["payload"]
    assert inserted["phone_number"] == "+15551234567"
    assert inserted["tenant_id"] == "t1"
    assert inserted["campaign_id"] == "c1"


@pytest.mark.asyncio
async def test_manage_lead_add_missing_phone():
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": "c1"}])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="add",
        phone_number=None,
        confirm=False,
    )
    assert "error" in result
    assert "phone_number" in result["error"]


@pytest.mark.asyncio
async def test_manage_lead_remove_preview_no_delete():
    """action=remove, confirm=False → preview, no delete."""
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": "c1"}])
    db.configure_response(
        "leads",
        "select",
        data=[
            {
                "id": "lead-1",
                "phone_number": "+15559999999",
                "first_name": "Bob",
                "last_name": "Smith",
                "status": "pending",
                "campaign_id": "c1",
            }
        ],
    )

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="remove",
        lead_id="lead-1",
        confirm=False,
    )
    assert result.get("preview") is True
    assert db._delete_calls == []
    assert db._update_calls == [], "update was called but confirm=False"
    change = result["changes"][0]
    assert change["before"]["id"] == "lead-1"
    # Preview shows the new state (soft-delete), not None
    assert change["after"] == {"status": "deleted"}


@pytest.mark.asyncio
async def test_manage_lead_remove_confirm_soft_deletes():
    """action=remove, confirm=True → soft-deletes lead (update status=deleted, NOT hard delete)."""
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": "c1"}])
    db.configure_response(
        "leads",
        "select",
        data=[{"id": "lead-1", "phone_number": "+15559999999", "first_name": "Bob",
               "last_name": "Smith", "status": "pending", "campaign_id": "c1"}],
    )

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="remove",
        lead_id="lead-1",
        confirm=True,
    )
    assert result.get("applied") is True
    # Must be a soft delete via update, not hard delete
    assert db._delete_calls == [], "Hard delete must not be used; use soft-delete (update status)"
    assert len(db._update_calls) == 1
    assert db._update_calls[0]["payload"] == {"status": "deleted"}


@pytest.mark.asyncio
async def test_manage_lead_unknown_action():
    db = FakeDbClient()
    db.configure_response("campaigns", "select", data=[{"id": "c1"}])

    result = await manage_lead(
        tenant_id="t1",
        db_client=db,
        campaign_id="c1",
        action="purge",  # not a valid action
        confirm=False,
    )
    assert "error" in result
