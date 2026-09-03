from __future__ import annotations

import copy
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaigns
from app.api.v1.schemas.campaigns import ApplyTtsConfigRequest


class _Query:
    def __init__(self, db: "_DB") -> None:
        self.db = db
        self.payload = None
        self.operation = "select"
        self.filters: list[tuple[str, object]] = []

    def select(self, _columns):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        if self.operation == "update" and self.db.convert_before_write:
            self.db.row["direction"] = "inbound"
            self.db.convert_before_write = False
        matched = all(self.db.row.get(column) == value for column, value in self.filters)
        if not matched:
            return SimpleNamespace(data=[], error=None)
        if self.operation == "update":
            self.db.row.update(self.payload)
        return SimpleNamespace(data=[dict(self.db.row)], error=None)


class _DB:
    def __init__(self) -> None:
        self.row = {
            "id": "campaign-1",
            "tenant_id": "tenant-1",
            "direction": "outbound",
            "status": "draft",
        }
        self.convert_before_write = True
        self.query: _Query | None = None
        self.queries: list[_Query] = []

    def table(self, name):
        assert name == "campaigns"
        self.query = _Query(self)
        self.queries.append(self.query)
        return self.query


class _CampaignTableQuery:
    def __init__(self, db: "_CampaignDB") -> None:
        self.db = db
        self.operation = "select"
        self.payload: dict[str, object] = {}
        self.filters: list[tuple[str, object]] = []
        self.in_filters: list[tuple[str, list[str]]] = []

    def select(self, _columns):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = dict(payload)
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.in_filters.append((column, [str(value) for value in values]))
        return self

    def execute(self):
        if self.operation == "update":
            target_id = next(
                (str(value) for column, value in self.filters if column == "id"),
                None,
            )
            if target_id == self.db.convert_on_adapter_update_id:
                self.db.rows[target_id]["direction"] = "inbound"
                self.db.convert_on_adapter_update_id = None

        matched = []
        for row in self.db.rows.values():
            if not all(str(row.get(column)) == str(value) for column, value in self.filters):
                continue
            if not all(
                str(row.get(column)) in values
                for column, values in self.in_filters
            ):
                continue
            matched.append(row)

        if self.operation == "update":
            self.db.adapter_update_calls += 1
            if self.db.suppress_adapter_update:
                return SimpleNamespace(data=[], error=None)
            for row in matched:
                row.update(self.payload)
        return SimpleNamespace(data=[dict(row) for row in matched], error=None)


class _AtomicConnection:
    def __init__(self, db: "_CampaignDB") -> None:
        self.db = db

    async def fetch(self, sql, *args):
        normalized_sql = " ".join(str(sql).split()).upper()
        if normalized_sql.startswith("SELECT"):
            tenant_id, campaign_ids = args
            requested = {str(value) for value in campaign_ids}
            return [
                {"id": campaign_id, "direction": row["direction"]}
                for campaign_id, row in self.db.rows.items()
                if campaign_id in requested
                and str(row["tenant_id"]) == str(tenant_id)
            ]
        if normalized_sql.startswith("UPDATE"):
            tenant_id, campaign_ids, provider, voice_id = args
            requested = {str(value) for value in campaign_ids}
            self.db.atomic_update_calls += 1
            updated = []
            for campaign_id, row in self.db.rows.items():
                if (
                    campaign_id in requested
                    and str(row["tenant_id"]) == str(tenant_id)
                    and row["direction"] == "outbound"
                ):
                    row["tts_provider"] = provider
                    row["voice_id"] = voice_id
                    updated.append({"id": campaign_id})
            return updated
        raise AssertionError(f"Unexpected SQL: {sql}")


class _CampaignDB:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = {str(row["id"]): dict(row) for row in rows}
        self.pool = object()
        self.adapter_update_calls = 0
        self.atomic_update_calls = 0
        self.convert_on_adapter_update_id: str | None = None
        self.convert_before_atomic_lock_id: str | None = None
        self.suppress_adapter_update = False
        self.connection = _AtomicConnection(self)

    def table(self, name):
        assert name == "campaigns"
        return _CampaignTableQuery(self)

    @asynccontextmanager
    async def acquire_with_tenant(self, pool, tenant_id):
        assert pool is self.pool
        if self.convert_before_atomic_lock_id is not None:
            self.rows[self.convert_before_atomic_lock_id]["direction"] = "inbound"
            self.convert_before_atomic_lock_id = None
        snapshot = copy.deepcopy(self.rows)
        try:
            yield self.connection
        except Exception:
            self.rows = snapshot
            raise


TENANT_1 = "10000000-0000-0000-0000-000000000001"
TENANT_2 = "20000000-0000-0000-0000-000000000002"
CAMPAIGN_1 = "30000000-0000-0000-0000-000000000001"
CAMPAIGN_2 = "30000000-0000-0000-0000-000000000002"


def _campaign_row(
    campaign_id: str,
    *,
    tenant_id: str = TENANT_1,
    direction: str = "outbound",
) -> dict[str, object]:
    return {
        "id": campaign_id,
        "tenant_id": tenant_id,
        "direction": direction,
        "tts_provider": "old-provider",
        "voice_id": "old-voice",
    }


def test_final_legacy_write_loses_to_committed_inbound_conversion() -> None:
    db = _DB()

    with pytest.raises(HTTPException) as exc:
        campaigns._update_owned_outbound_campaign(
            db,
            campaign_id="campaign-1",
            tenant_id="tenant-1",
            payload={"status": "paused"},
            operation="pause campaign",
        )

    assert exc.value.status_code == 409
    assert db.row["status"] == "draft"
    assert any(("direction", "outbound") in query.filters for query in db.queries)
    assert all(("tenant_id", "tenant-1") in query.filters for query in db.queries)


def test_contact_zero_row_write_keeps_contact_conflict_contract() -> None:
    response = SimpleNamespace(data=[], error=None)

    with pytest.raises(HTTPException) as exc:
        campaigns._require_contact_write(response, CAMPAIGN_1, "update")

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "contact_write_conflict"


@pytest.mark.parametrize(
    ("rows", "campaign_id"),
    [
        ([], CAMPAIGN_1),
        ([_campaign_row(CAMPAIGN_1, tenant_id=TENANT_2)], CAMPAIGN_1),
    ],
    ids=["missing", "foreign-tenant"],
)
def test_final_legacy_write_hides_missing_and_foreign_ids_as_404(
    rows,
    campaign_id,
) -> None:
    db = _CampaignDB(rows)

    with pytest.raises(HTTPException) as exc:
        campaigns._update_owned_outbound_campaign(
            db,
            campaign_id=campaign_id,
            tenant_id=TENANT_1,
            payload={"status": "paused"},
            operation="pause campaign",
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Campaign not found"


def test_final_legacy_write_uses_inbound_error_only_for_owned_inbound_row() -> None:
    db = _CampaignDB([_campaign_row(CAMPAIGN_1, direction="inbound")])

    with pytest.raises(HTTPException) as exc:
        campaigns._update_owned_outbound_campaign(
            db,
            campaign_id=CAMPAIGN_1,
            tenant_id=TENANT_1,
            payload={"status": "paused"},
            operation="pause campaign",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert exc.value.detail["campaign_ids"] == [CAMPAIGN_1]


def test_final_legacy_write_does_not_mislabel_other_zero_row_conflict_as_inbound() -> None:
    db = _CampaignDB([_campaign_row(CAMPAIGN_1)])
    db.suppress_adapter_update = True

    with pytest.raises(HTTPException) as exc:
        campaigns._update_owned_outbound_campaign(
            db,
            campaign_id=CAMPAIGN_1,
            tenant_id=TENANT_1,
            payload={"status": "paused"},
            operation="pause campaign",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "campaign_write_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extra_row", "expected_status", "concurrent"),
    [
        (None, 404, False),
        (_campaign_row(CAMPAIGN_2, tenant_id=TENANT_2), 404, False),
        (_campaign_row(CAMPAIGN_2, direction="inbound"), 409, False),
        (_campaign_row(CAMPAIGN_2), 409, True),
    ],
    ids=["missing", "foreign-tenant", "already-inbound", "concurrently-converted"],
)
async def test_apply_tts_config_rejects_mixed_id_sets_without_partial_writes(
    monkeypatch,
    extra_row,
    expected_status,
    concurrent,
) -> None:
    rows = [_campaign_row(CAMPAIGN_1)]
    if extra_row is not None:
        rows.append(extra_row)
    db = _CampaignDB(rows)
    if concurrent:
        db.convert_on_adapter_update_id = CAMPAIGN_2
        db.convert_before_atomic_lock_id = CAMPAIGN_2
    monkeypatch.setattr(campaigns, "acquire_with_tenant", db.acquire_with_tenant, raising=False)
    monkeypatch.setattr(
        campaigns,
        "_valid_voice_ids_for_provider",
        AsyncMock(return_value={"new-voice"}),
    )

    with pytest.raises(HTTPException) as exc:
        await campaigns.apply_tts_config(
            ApplyTtsConfigRequest(
                tts_provider="new-provider",
                tts_voice_id="new-voice",
                campaign_ids=[CAMPAIGN_1, CAMPAIGN_2],
            ),
            SimpleNamespace(),
            CurrentUser(
                id="user-1",
                email="user@example.test",
                tenant_id=TENANT_1,
                role="owner",
            ),
            db,
        )

    assert exc.value.status_code == expected_status
    assert db.rows[CAMPAIGN_1]["tts_provider"] == "old-provider"
    assert db.rows[CAMPAIGN_1]["voice_id"] == "old-voice"


@pytest.mark.asyncio
async def test_apply_tts_config_updates_exact_set_in_one_atomic_statement(
    monkeypatch,
) -> None:
    db = _CampaignDB([_campaign_row(CAMPAIGN_1), _campaign_row(CAMPAIGN_2)])
    monkeypatch.setattr(campaigns, "acquire_with_tenant", db.acquire_with_tenant, raising=False)
    monkeypatch.setattr(
        campaigns,
        "_valid_voice_ids_for_provider",
        AsyncMock(return_value={"new-voice"}),
    )

    result = await campaigns.apply_tts_config(
        ApplyTtsConfigRequest(
            tts_provider="new-provider",
            tts_voice_id="new-voice",
            campaign_ids=[CAMPAIGN_1, CAMPAIGN_2],
        ),
        SimpleNamespace(),
        CurrentUser(
            id="user-1",
            email="user@example.test",
            tenant_id=TENANT_1,
            role="owner",
        ),
        db,
    )

    assert result["updated"] == [CAMPAIGN_1, CAMPAIGN_2]
    assert result["count"] == 2
    assert db.atomic_update_calls == 1
    assert db.adapter_update_calls == 0
    assert all(row["tts_provider"] == "new-provider" for row in db.rows.values())
    assert all(row["voice_id"] == "new-voice" for row in db.rows.values())


@pytest.mark.parametrize(
    "endpoint",
    [
        campaigns.update_campaign,
        campaigns.start_campaign,
        campaigns.delete_campaign,
    ],
)
def test_every_legacy_campaign_row_mutation_uses_final_direction_guard(endpoint) -> None:
    source = inspect.getsource(endpoint)

    assert "_update_owned_outbound_campaign(" in source
    assert 'table("campaigns").update' not in source


def test_bulk_tts_mutation_uses_exact_set_atomic_helper() -> None:
    source = inspect.getsource(campaigns.apply_tts_config)

    assert "_apply_tts_config_atomically(" in source
    assert "_update_owned_outbound_campaign(" not in source


@pytest.mark.asyncio
async def test_get_campaign_preserves_missing_tenant_client_error(monkeypatch) -> None:
    monkeypatch.setattr(
        campaigns,
        "_get_campaign_service",
        lambda _db: pytest.fail("service must not run without tenant context"),
    )
    user = CurrentUser(
        id="platform-user",
        email="platform@example.test",
        tenant_id=None,
        role="platform_admin",
    )

    with pytest.raises(HTTPException) as exc:
        await campaigns.get_campaign(
            "campaign-1",
            SimpleNamespace(),
            user,
            SimpleNamespace(),
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Current user is not associated with a tenant"
