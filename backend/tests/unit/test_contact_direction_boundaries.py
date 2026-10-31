"""Behavioural boundaries between outbound contacts and inbound campaigns."""

from __future__ import annotations

import io
import inspect

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaigns as campaigns_ep
from app.api.v1.endpoints import contact_lists as lists_ep
from app.api.v1.endpoints import contacts as contacts_ep
from app.api.v1.schemas.campaigns import ContactCreate, ContactUpdate
from app.core.security.rbac import Permission


class _Response:
    def __init__(self, data, *, count=None, error=None):
        self.data = data
        self.count = len(data) if count is None and isinstance(data, list) else count
        self.error = error


class _Query:
    def __init__(self, db: "_DB", table: str):
        self.db = db
        self.table = table
        self.filters: list[tuple[str, str, object]] = []
        self.operation = "select"
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self.filters.append(("neq", column, value))
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, tuple(values)))
        return self

    def is_(self, column, value):
        self.filters.append(("is", column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def _matches(self, row: dict) -> bool:
        for operation, column, value in self.filters:
            actual = row.get(column)
            if operation == "eq" and actual != value:
                return False
            if operation == "neq" and actual == value:
                return False
            if operation == "in" and actual not in value:
                return False
            if operation == "is" and actual is not value:
                return False
        return True

    def execute(self):
        self.db.reads.append(self.table)
        rows = self.db.tables.setdefault(self.table, [])
        if self.operation == "select":
            if self.db.read_error:
                return _Response([], error=self.db.read_error)
            matched = [dict(row) for row in rows if self._matches(row)]
            return _Response(matched, count=len(matched))

        self.db.writes.append((self.table, self.operation, self.payload))
        self.db.write_filters.append((self.table, self.operation, list(self.filters)))
        if self.db.write_error:
            return _Response([], error=self.db.write_error)
        if self.db.write_zero_rows:
            return _Response([])
        payloads = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.operation == "insert":
            rows.extend(dict(payload) for payload in payloads)
            return _Response([dict(payload) for payload in payloads])

        changed = []
        for row in rows:
            if self._matches(row):
                row.update(self.payload)
                changed.append(dict(row))
        return _Response(changed)


class _DB:
    def __init__(
        self,
        *,
        direction: str | None = "outbound",
        include_campaign: bool = True,
        read_error: str | None = None,
        write_error: str | None = None,
        write_zero_rows: bool = False,
    ):
        campaign = {
            "id": "c1",
            "tenant_id": "t1",
            "name": "Campaign",
            "status": "draft",
            "direction": direction,
            "script_config": {"campaign_slots": {"default_country_code": "GB"}},
        }
        self.tables = {
            "campaigns": [campaign] if include_campaign else [],
            "leads": [
                {
                    "id": "lead1",
                    "tenant_id": "t1",
                    "campaign_id": "c1",
                    "phone_number": "+447700900123",
                    "status": "pending",
                }
            ],
            "contact_lists": [
                {
                    "id": "list1",
                    "tenant_id": "t1",
                    "campaign_id": "c1",
                    "name": "List",
                    "source": "csv",
                    "is_active": False,
                }
            ],
            "dialer_jobs": [],
        }
        self.reads: list[str] = []
        self.writes: list[tuple[str, str, object]] = []
        self.write_filters: list[tuple[str, str, list[tuple[str, str, object]]]] = []
        self.read_error = read_error
        self.write_error = write_error
        self.write_zero_rows = write_zero_rows

    def table(self, name):
        return _Query(self, name)


USER = CurrentUser(
    id="u1",
    email="user@example.test",
    tenant_id="t1",
    role="campaign_manager",
)


def _upload(contents: str = "phone_number\n07700 900123\n") -> UploadFile:
    return UploadFile(file=io.BytesIO(contents.encode()), filename="contacts.csv")


async def _invoke_contact_mutation(name: str, db: _DB):
    if name == "add":
        return await campaigns_ep.add_contact_to_campaign(
            "c1", ContactCreate(phone_number="07700 900123"), USER, db
        )
    if name == "edit":
        return await campaigns_ep.update_contact_in_campaign(
            "c1", "lead1", ContactUpdate(first_name="Changed"), USER, db
        )
    if name == "delete":
        return await campaigns_ep.remove_contact_from_campaign("c1", "lead1", USER, db)
    if name == "upload":
        return await contacts_ep.upload_campaign_contacts(
            campaign_id="c1",
            file=_upload(),
            skip_duplicates=True,
            current_user=USER,
            db_client=db,
        )
    if name == "paste":
        return await contacts_ep.paste_campaign_contacts(
            campaign_id="c1",
            body=contacts_ep.BulkPasteRequest(text="07700 900123"),
            current_user=USER,
            db_client=db,
        )
    if name == "bulk":
        return await contacts_ep.bulk_import_contacts(
            file=_upload(), campaign_id="c1", current_user=USER, db_client=db
        )
    raise AssertionError(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["add", "edit", "delete", "upload", "paste", "bulk"])
async def test_contact_mutations_reject_inbound_before_any_write(name):
    db = _DB(direction="inbound")

    with pytest.raises(HTTPException) as exc:
        await _invoke_contact_mutation(name, db)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert db.writes == []
    assert db.reads[0] == "campaigns"


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", [None, "", "   "])
async def test_contact_mutations_fail_closed_on_explicit_invalid_direction(direction):
    db = _DB(direction=direction)

    with pytest.raises(HTTPException) as exc:
        await _invoke_contact_mutation("add", db)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert db.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["add", "edit", "delete", "upload", "paste", "bulk"])
async def test_contact_mutations_hide_missing_or_foreign_campaign_before_any_write(name):
    db = _DB(include_campaign=False)

    with pytest.raises(HTTPException) as exc:
        await _invoke_contact_mutation(name, db)

    assert exc.value.status_code == 404
    assert db.writes == []
    assert db.reads[0] == "campaigns"


@pytest.mark.asyncio
async def test_contact_campaign_guard_rejects_missing_tenant_before_any_read_or_write():
    db = _DB(direction="outbound")
    tenantless_user = CurrentUser(
        id="platform-user",
        email="platform@example.test",
        tenant_id=None,
        role="platform_admin",
    )

    with pytest.raises(HTTPException) as exc:
        await campaigns_ep.add_contact_to_campaign(
            "c1",
            ContactCreate(phone_number="07700 900123"),
            tenantless_user,
            db,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant context required for this operation"
    assert db.reads == []
    assert db.writes == []


@pytest.mark.asyncio
async def test_contact_campaign_guard_does_not_report_database_failure_as_not_found():
    db = _DB(read_error="database unavailable")

    with pytest.raises(HTTPException) as exc:
        await _invoke_contact_mutation("add", db)

    assert exc.value.status_code == 503
    assert exc.value.detail == "Campaign direction check unavailable"
    assert db.writes == []


@pytest.mark.asyncio
async def test_legacy_bulk_uses_campaign_country_and_canonical_phone_normalizer():
    db = _DB(direction="outbound")
    db.tables["leads"] = []

    result = await contacts_ep.bulk_import_contacts(
        file=_upload(), campaign_id="c1", current_user=USER, db_client=db
    )

    assert result.imported == 1
    inserted = next(payload for table, operation, payload in db.writes if table == "leads")
    assert inserted["phone_number"] == "+447700900123"


@pytest.mark.asyncio
async def test_legacy_bulk_never_counts_failed_write_as_imported():
    db = _DB(direction="outbound", write_error="direction guard rejected insert")
    db.tables["leads"] = []

    result = await contacts_ep.bulk_import_contacts(
        file=_upload(), campaign_id="c1", current_user=USER, db_client=db
    )

    assert result.imported == 0
    assert result.failed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["upload", "paste", "bulk"])
async def test_bulk_contact_routes_report_direction_change_as_conflict(name):
    db = _DB(
        direction="outbound",
        write_error="constraint leads_outbound_campaign_guard rejected write",
    )
    db.tables["leads"] = []

    with pytest.raises(HTTPException) as exc:
        await _invoke_contact_mutation(name, db)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert exc.value.detail["campaign_ids"] == ["c1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["upload", "paste"])
async def test_grouped_import_reports_list_direction_change_as_conflict(name):
    db = _DB(
        direction="outbound",
        write_error="constraint contact_lists_outbound_campaign_guard rejected write",
    )
    db.tables["leads"] = []

    with pytest.raises(HTTPException) as exc:
        await _invoke_contact_mutation(name, db)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert exc.value.detail["campaign_ids"] == ["c1"]


def test_create_contact_list_never_returns_fabricated_id_after_failed_insert():
    db = _DB(direction="outbound", write_error="direction guard rejected insert")

    result = lists_ep.create_contact_list(
        db,
        campaign_id="c1",
        tenant_id="t1",
        name="new-list.csv",
        source="csv",
    )

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["revive", "edit", "delete"])
async def test_final_lead_update_rechecks_tenant_and_campaign(name):
    db = _DB(direction="outbound")
    if name == "revive":
        db.tables["leads"][0]["status"] = "deleted"
        await _invoke_contact_mutation("add", db)
    else:
        await _invoke_contact_mutation(name, db)

    filters = next(
        filters
        for table, operation, filters in db.write_filters
        if table == "leads" and operation == "update"
    )
    assert ("eq", "tenant_id", "t1") in filters
    assert ("eq", "campaign_id", "c1") in filters


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["add", "edit", "delete"])
async def test_contact_update_never_claims_success_after_direction_trigger_rejection(name):
    db = _DB(
        direction="outbound",
        write_error="constraint leads_outbound_campaign_guard rejected write",
    )
    if name == "add":
        db.tables["leads"] = []

    with pytest.raises(HTTPException) as exc:
        await _invoke_contact_mutation(name, db)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"


def test_legacy_bulk_route_requires_campaign_update_permission():
    route = next(route for route in contacts_ep.router.routes if route.path == "/contacts/bulk")
    closure_permissions = {
        inspect.getclosurevars(dependency.call).nonlocals.get("permission")
        for dependency in route.dependant.dependencies
    }
    assert Permission.CAMPAIGNS_UPDATE in closure_permissions


@pytest.mark.parametrize(
    ("router", "path", "method"),
    [
        (
            contacts_ep.router,
            "/contacts/campaigns/{campaign_id}/upload",
            "POST",
        ),
        (
            contacts_ep.router,
            "/contacts/campaigns/{campaign_id}/paste",
            "POST",
        ),
        (lists_ep.router, "/contact-lists/{list_id}", "PATCH"),
        (lists_ep.router, "/contact-lists/{list_id}/call", "POST"),
    ],
)
def test_every_contact_mutation_route_requires_campaign_update_permission(
    router, path, method
):
    route = next(
        route
        for route in router.routes
        if route.path == path and method in route.methods
    )
    closure_permissions = {
        inspect.getclosurevars(dependency.call).nonlocals.get("permission")
        for dependency in route.dependant.dependencies
    }
    assert Permission.CAMPAIGNS_UPDATE in closure_permissions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("db", "expected_status"),
    [
        (_DB(direction="outbound", write_error="database unavailable"), 503),
        (_DB(direction="outbound", write_zero_rows=True), 409),
    ],
)
async def test_toggle_list_never_claims_success_when_write_is_unconfirmed(
    db, expected_status
):
    with pytest.raises(HTTPException) as exc:
        await lists_ep.toggle_contact_list(
            "list1",
            lists_ep.ContactListToggle(is_active=True),
            USER,
            db,
        )

    assert exc.value.status_code == expected_status


@pytest.mark.asyncio
async def test_call_list_rejects_inbound_before_activation():
    db = _DB(direction="inbound")

    with pytest.raises(HTTPException) as exc:
        await lists_ep.call_contact_list("list1", USER, db)

    assert exc.value.status_code == 409
    assert db.writes == []


@pytest.mark.asyncio
async def test_call_list_propagates_campaign_http_conflict(monkeypatch):
    db = _DB(direction="outbound")

    class _Service:
        async def start_campaign(self, **_kwargs):
            raise HTTPException(status_code=409, detail="campaign conflict")

    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: _Service())

    with pytest.raises(HTTPException) as exc:
        await lists_ep.call_contact_list("list1", USER, db)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_call_list_never_proceeds_after_conversion_trigger_rejects_activation(
    monkeypatch,
):
    db = _DB(
        direction="outbound",
        write_error="constraint contact_lists_outbound_campaign_guard rejected write",
    )
    started = False

    class _Service:
        async def start_campaign(self, **_kwargs):
            nonlocal started
            started = True

    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: _Service())

    with pytest.raises(HTTPException) as exc:
        await lists_ep.call_contact_list("list1", USER, db)

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "inbound_campaign_managed_separately"
    assert started is False


@pytest.mark.asyncio
async def test_call_list_reports_enqueue_failure_as_explicit_partial_state(monkeypatch):
    db = _DB(direction="outbound")

    class _Service:
        async def start_campaign(self, **_kwargs):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(campaigns_ep, "_get_campaign_service", lambda _db: _Service())

    with pytest.raises(HTTPException) as exc:
        await lists_ep.call_contact_list("list1", USER, db)

    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "error": "contact_list_start_failed_after_activation",
        "message": (
            "The list was activated, but its contacts were not queued. "
            "Retry this action; do not assume a later campaign start will dial them."
        ),
        "list_id": "list1",
        "campaign_id": "c1",
        "is_active": True,
        "jobs_enqueued": 0,
    }
